"""The report page: titles of what someone is working on, for a status update (REQ-013, D-083)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import briefing_service as brief
import people_admin
import todo_service as svc
import webapp
from database import Segment
from tests.helpers import add_recording

NOW = dt.datetime(2026, 11, 27, 9, 0, tzinfo=dt.timezone.utc)


def make(session, title, status=None, **kw):
    todo = svc.create_todo(session, NOW, title=title, actor="owner", allow_similar=True, **kw)
    if status:
        svc.update_todo(session, NOW, todo.id, actor="owner", status=status)
    return todo.id


def owner_tasks(session):
    make(session, "Folien bauen", priority=3)
    make(session, "Angebot schreiben", priority=2, status="in_progress")
    make(session, "Budget prüfen", priority=1)
    make(session, "Altes Thema", status="done")
    make(session, "Verworfen", status="dropped")
    make(session, "Urlaub planen", topic="Privat")
    svc.set_topic_excluded(session, "Privat", True)


def anna_tasks(session):
    make(session, "Zahlen liefern", kind="waiting", people=["Anna"])
    make(session, "Vertrag prüfen", kind="waiting", people=["Anna"], status="in_progress")
    make(session, "Schon geliefert", kind="waiting", people=["Anna"], status="done")
    make(session, "Anna anrufen", people=["Anna"])
    make(session, "Bericht schicken", kind="waiting", people=["Jochen"])


# --- the service ---------------------------------------------------------------------------------------
def test_req_013_by_default_the_report_lists_the_owners_unfinished_tasks_by_priority(db_session):
    owner_tasks(db_session)
    anna_tasks(db_session)
    report = brief.status_report(db_session, NOW)
    assert report.person is None
    assert report.in_progress == ["Angebot schreiben"]
    assert report.open == ["Budget prüfen", "Folien bauen", "Anna anrufen"]
    assert not report.truncated


def test_req_013_for_another_person_the_report_lists_what_they_promised(db_session):
    owner_tasks(db_session)
    anna_tasks(db_session)
    report = brief.status_report(db_session, NOW, "Anna")
    assert (report.person, report.in_progress, report.open) == ("Anna", ["Vertrag prüfen"], ["Zahlen liefern"])


def test_req_013_a_long_report_is_cut_and_says_so(db_session, monkeypatch):
    monkeypatch.setattr(svc, "LIST_LIMIT_MAX", 2)
    for n in range(3):
        make(db_session, f"Task {n}")
    report = brief.status_report(db_session, NOW)
    assert report.open == ["Task 0", "Task 1"] and report.truncated


# --- the page ------------------------------------------------------------------------------------------
@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    webapp.app.dependency_overrides[webapp.get_session] = override
    yield TestClient(webapp.app, base_url="http://localhost")
    webapp.app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory):
    with session_factory() as s:
        owner_tasks(s)
        anna_tasks(s)
        make(s, "Ask Eve", people=["Eve"])
        people_admin.set_person_excluded(s, "Eve", True)
        rec = add_recording(s, 1, segments=2)
        first = s.query(Segment).filter(Segment.recording_id == rec.id).order_by(Segment.id).first()
        first.speaker = "Bea <Berg>"
        s.commit()


def report_card(html):
    return html[html.index('<div class="card report">'):html.index("</div>", html.index('<div class="card report">'))]


def test_req_013_report_is_in_the_navigation_after_people(client, world):
    html = client.get("/report").text
    nav = html[html.index('<header class="top">'):html.index("</header>")]
    assert nav.index('href="/people"') < nav.index('href="/report"') < nav.index('href="/topics"')
    assert '<a class="nav current" href="/report">Report</a>' in nav


def test_req_013_the_page_shows_me_by_default_with_my_tasks_as_bullets(client, world):
    html = client.get("/report").text
    select = html[html.index('<select name="person"'):html.index("</select>")]
    assert '<option value="">Ich</option>' in select and " selected" not in select
    assert "Deine offenen Aufgaben." in html
    card = report_card(html)
    assert "<h2>In Arbeit</h2>\n  <ul><li>Angebot schreiben</li></ul>" in card
    assert "<h2>Offen</h2>\n  <ul><li>Budget prüfen</li><li>Folien bauen</li><li>Anna anrufen</li><li>Ask Eve</li></ul>" in card
    for hidden in ("Altes Thema", "Verworfen", "Urlaub planen", "Zahlen liefern"):
        assert hidden not in card


def test_req_013_picking_a_person_shows_what_they_promised(client, world):
    html = client.get("/report?person=anna").text
    assert '<option value="Anna" selected>Anna</option>' in html and "Was Anna Dir zugesagt hat." in html
    card = report_card(html)
    assert "<li>Vertrag prüfen</li>" in card and "<li>Zahlen liefern</li>" in card
    assert "Anna anrufen" not in card and "Schon geliefert" not in card


def test_req_013_the_dropdown_lists_me_then_the_known_people_without_excluded_and_unnamed(client, world):
    html = client.get("/report").text
    select = html[html.index('<select name="person"'):html.index("</select>")]
    options = [part.split(">", 1)[1].split("<", 1)[0] for part in select.split("<option")[1:]]
    assert options == ["Ich", "Anna", "Bea &lt;Berg&gt;", "Jochen"]
    assert "Eve" not in select and "Speaker" not in select


@pytest.mark.parametrize("name", ["Nobody", "Eve", "Speaker 1"])
def test_req_013_a_name_that_is_not_a_known_person_is_a_404(client, world, name):
    assert client.get("/report", params={"person": name}).status_code == 404


def test_req_013_titles_are_plain_escaped_text_without_links(client, session_factory):
    with session_factory() as s:
        make(s, "<b>Fett</b> & mehr")
    card = report_card(client.get("/report").text)
    assert "<li>&lt;b&gt;Fett&lt;/b&gt; &amp; mehr</li>" in card and "<a" not in card


def test_req_013_nothing_open_says_so(client, world):
    html = client.get("/report?person=Jochen").text
    assert "<li>Bericht schicken</li>" in html
    html = client.get("/report", params={"person": "Bea <Berg>"}).text
    assert '<div class="card empty">Gerade nichts offen.</div>' in html and '<div class="card report">' not in html


def test_req_013_a_long_page_says_that_there_are_more(client, session_factory, monkeypatch):
    monkeypatch.setattr(svc, "LIST_LIMIT_MAX", 1)
    with session_factory() as s:
        make(s, "One")
        make(s, "Two")
    card = report_card(client.get("/report").text)
    assert "<li>One</li>" in card and "Two" not in card and "… und weitere" in card
