"""Deleting all data after one confirmation (REQ-011, D-079): the service and the admin page."""
import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import webapp
import wipe_service
from database import (AnalysisRun, Digest, ExcludedPerson, GlossaryAlias, GlossarySuggestion, GlossaryTerm, LlmTrace,
                      MindmapNode, OAuthCode, OAuthToken, PersonMapNode, PersonProfile, PersonTopicHidden,
                      PersonTopicNote, PlaudSession, Recording, Segment, SyncRequest, TextVersion, Todo, TodoEvent,
                      TodoLink, Topic, TopicNote)
from tests.helpers import add_recording

T = dt.datetime(2026, 11, 24, 9, 0, tzinfo=dt.timezone.utc)

BUILT_FROM_RECORDINGS = [Todo, TodoLink, TodoEvent, Topic, TopicNote, PersonProfile, PersonTopicNote, PersonTopicHidden,
                         ExcludedPerson, MindmapNode, PersonMapNode, Digest, TextVersion, GlossaryTerm, GlossaryAlias,
                         GlossarySuggestion]
KEPT = [PlaudSession, OAuthCode, OAuthToken, SyncRequest, AnalysisRun, LlmTrace]


def _fill(session):
    """Two kept recordings and one discarded one; one or two rows in every other table."""
    first = add_recording(session, 1, segments=3)
    add_recording(session, 2, segments=2)
    add_recording(session, 3, segments=0, title="", summary="", discarded_at=T)
    topic = Topic(name="Budget")
    session.add(topic)
    session.flush()
    tasks = [Todo(title="Angebot schreiben", topic_id=topic.id, recording_id=first.id, people="Anna"),
             Todo(title="Termin finden", people="Anna")]
    session.add_all(tasks)
    session.flush()
    run = AnalysisRun(started_at=T, status="ok")
    session.add(run)
    session.flush()
    term = GlossaryTerm(canonical="Plaud")
    session.add(term)
    session.flush()
    session.add_all([
        TodoLink(low_id=tasks[0].id, high_id=tasks[1].id),
        TodoEvent(todo_id=tasks[0].id, kind="create", actor="claude"),
        TopicNote(topic_id=topic.id, recording_id=first.id, note="Budget steht"),
        PersonProfile(name="Anna", agreements="Angebot bis Freitag"),
        PersonTopicNote(person="anna", topic_id=topic.id, note="wartet auf Zahlen"),
        PersonTopicHidden(person="anna", topic_id=topic.id),
        ExcludedPerson(name="Eve"),
        MindmapNode(node_key=f"t{tasks[0].id}", x=1.0, y=2.0),
        PersonMapNode(person="anna", node_key="person", x=0.0, y=0.0),
        Digest(day=dt.date(2026, 11, 24), body="Ein Gespräch mit Anna"),
        TextVersion(kind="digest", ref="2026-11-24", body="ältere Fassung", saved_at=T),
        GlossaryAlias(term_id=term.id, alias="Plot"),
        GlossarySuggestion(wrong="Plod", right="Plaud"),
        PlaudSession(id=1, access_token="access", refresh_token="refresh"),
        OAuthCode(code_hash="c" * 64, redirect_uri="https://claude.ai/cb", code_challenge="x", expires_at=T),
        OAuthToken(token_hash="h" * 64, kind="access", family_id="f", expires_at=T),
        SyncRequest(requested_at=T),
        LlmTrace(ts_utc=T, call_type="analysis", run_id=run.id, status="ok"),
    ])
    session.commit()


def _rows(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def filled(db_session):
    _fill(db_session)
    return db_session


def test_req_011_wipe_discards_every_kept_recording_and_keeps_the_row(filled):
    wipe_service.wipe_all(filled, T)
    recordings = filled.scalars(select(Recording).order_by(Recording.id)).all()
    assert [r.plaud_id for r in recordings] == ["p1", "p2", "p3"]
    assert all(r.discarded_at is not None and (r.title, r.summary, r.segment_count) == ("", "", 0) for r in recordings)
    assert _rows(filled, Segment) == 0


@pytest.mark.parametrize("model", BUILT_FROM_RECORDINGS, ids=lambda m: m.__tablename__)
def test_req_011_wipe_empties_every_table_built_from_the_recordings(filled, model):
    assert _rows(filled, model) > 0
    wipe_service.wipe_all(filled, T)
    assert _rows(filled, model) == 0


@pytest.mark.parametrize("model", KEPT, ids=lambda m: m.__tablename__)
def test_req_011_wipe_keeps_login_and_logs(filled, model):
    before = _rows(filled, model)
    wipe_service.wipe_all(filled, T)
    assert before == 1 and _rows(filled, model) == 1


def test_req_011_counts_name_what_a_wipe_takes_and_nothing_after_it(filled):
    found = wipe_service.counts(filled)
    shown = {k: found[k] for k in ("recordings", "todos", "topics", "person_profiles", "digests", "glossary_terms")}
    assert shown == {"recordings": 2, "todos": 2, "topics": 1, "person_profiles": 1, "digests": 1, "glossary_terms": 1}
    wipe_service.wipe_all(filled, T)
    assert not any(wipe_service.counts(filled).values())


# --- the admin page ------------------------------------------------------------------------------------
@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    with session_factory() as s:
        _fill(s)
    webapp.app.dependency_overrides[webapp.get_session] = override
    yield TestClient(webapp.app, base_url="http://localhost")
    webapp.app.dependency_overrides.clear()


def _shown_fingerprint(client) -> str:
    return re.search(r'name="fingerprint" value="([0-9a-f]+)"', client.get("/admin/wipe").text).group(1)


def test_req_011_admin_has_the_wipe_tile(client):
    html = client.get("/admin").text
    assert 'href="/admin/wipe"' in html and "Alle Daten löschen" in html


def test_req_011_confirm_page_lists_the_numbers_and_deletes_nothing(client, session_factory):
    response = client.get("/admin/wipe")
    html = response.text
    assert response.status_code == 200 and "Wirklich alle Daten löschen?" in html
    assert all(line in html for line in ("Aufnahmen: 2 ", "Aufgaben: 2,", "Themen: 1,", "Personenprofile: 1,",
                                         "Tagesübersichten: 1<", "Schreibweisen: 1,"))
    assert "Plaud-Anmeldung" in html and "Ja, alles endgültig löschen" in html and 'href="/admin">Abbrechen' in html
    with session_factory() as s:
        assert _rows(s, Todo) == 2 and _rows(s, Segment) == 5


def test_req_011_button_wipes_and_the_page_then_says_nothing_is_left(client, session_factory):
    response = client.post("/admin/wipe", data={"csrf": webapp.csrf_token(), "fingerprint": _shown_fingerprint(client)},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/admin/wipe"
    with session_factory() as s:
        assert _rows(s, Todo) == 0 and _rows(s, Segment) == 0 and _rows(s, PlaudSession) == 1
    html = client.get("/admin/wipe").text
    assert "Es sind keine Daten mehr gespeichert." in html and 'action="/admin/wipe"' not in html


def test_req_011_a_stale_page_deletes_nothing(client, session_factory):
    shown = _shown_fingerprint(client)
    with session_factory() as s:  # the analysis writes a task while the owner looks at the page
        s.add(Todo(title="Neue Aufgabe"))
        s.commit()
    response = client.post("/admin/wipe", data={"csrf": webapp.csrf_token(), "fingerprint": shown})
    assert response.status_code == 409 and "Es wurde nichts gelöscht." in response.text and "Aufgaben: 3," in response.text
    with session_factory() as s:
        assert _rows(s, Todo) == 3 and _rows(s, Segment) == 5


@pytest.mark.parametrize("csrf", [None, "", "wrong"])
def test_req_011_wipe_without_a_valid_token_is_refused(client, session_factory, csrf):
    data = {"fingerprint": _shown_fingerprint(client)} | ({} if csrf is None else {"csrf": csrf})
    assert client.post("/admin/wipe", data=data).status_code == 403
    with session_factory() as s:
        assert _rows(s, Todo) == 2 and _rows(s, Segment) == 5


@pytest.mark.parametrize("fingerprint", ["", "ü" * 16, "0" * 33])
def test_req_011_a_malformed_fingerprint_is_a_clean_error(client, session_factory, fingerprint):
    response = client.post("/admin/wipe", data={"csrf": webapp.csrf_token(), "fingerprint": fingerprint})
    assert response.status_code == 400
    with session_factory() as s:
        assert _rows(s, Todo) == 2
