"""The report page: titles of what someone is working on, for a status update (REQ-013, D-083), with what was
finished lately and a copy button (REQ-014, D-084)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import briefing_service as brief
import people_admin
import todo_service as svc
import webapp
from database import Segment
from tests.helpers import add_recording
from utils import now_utc

NOW = dt.datetime(2026, 11, 27, 9, 0, tzinfo=dt.timezone.utc)


def make(session, title, status=None, now=NOW, ago=dt.timedelta(0), **kw):
    """A task created ten days before `now`; its status changes `ago` before `now`."""
    todo = svc.create_todo(session, now - dt.timedelta(days=10), title=title, actor="owner", allow_similar=True, **kw)
    if status:
        svc.update_todo(session, now - ago, todo.id, actor="owner", status=status)
    return todo.id


def owner_tasks(session, now=NOW):
    make(session, "Folien bauen", priority=3, now=now)
    make(session, "Angebot schreiben", priority=2, status="in_progress", now=now)
    make(session, "Budget prüfen", priority=1, now=now)
    make(session, "Altes Thema", status="done", now=now, ago=dt.timedelta(days=2))
    make(session, "Rechnung geprüft", status="done", now=now, ago=dt.timedelta(hours=1))
    make(session, "Lange her", status="done", now=now, ago=dt.timedelta(days=8))
    make(session, "Verworfen", status="dropped", now=now)
    make(session, "Urlaub planen", topic="Privat", now=now)
    make(session, "Urlaub gebucht", topic="Privat", status="done", now=now)
    svc.set_topic_excluded(session, "Privat", True)


def anna_tasks(session, now=NOW):
    make(session, "Zahlen liefern", kind="waiting", people=["Anna"], now=now)
    make(session, "Vertrag prüfen", kind="waiting", people=["Anna"], status="in_progress", now=now)
    make(session, "Schon geliefert", kind="waiting", people=["Anna"], status="done", now=now, ago=dt.timedelta(days=1))
    make(session, "Anna anrufen", people=["Anna"], now=now)
    make(session, "Mit Anna fertig", people=["Anna"], status="done", now=now)
    make(session, "Bericht schicken", kind="waiting", people=["Jochen"], now=now)


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


def test_req_014_the_report_lists_what_was_finished_in_the_last_seven_days_newest_first(db_session):
    owner_tasks(db_session)
    anna_tasks(db_session)
    report = brief.status_report(db_session, NOW)
    assert report.done == ["Mit Anna fertig", "Rechnung geprüft", "Altes Thema"] and report.done_days == 7
    assert brief.status_report(db_session, NOW, "Anna").done == ["Schon geliefert"]


def test_req_014_the_groups_read_done_then_in_progress_then_open_without_empty_ones(db_session):
    owner_tasks(db_session)
    assert [key for key, _ in brief.status_report(db_session, NOW).groups()] == ["done", "in_progress", "open"]
    make(db_session, "Nur offen", kind="waiting", people=["Jochen"])
    assert brief.status_report(db_session, NOW, "Jochen").groups() == [("open", ["Nur offen"])]


def test_req_014_a_long_list_of_finished_tasks_is_cut_too(db_session, monkeypatch):
    monkeypatch.setattr(svc, "LIST_LIMIT_MAX", 1)
    make(db_session, "One", status="done")
    make(db_session, "Two", status="done")
    assert brief.status_report(db_session, NOW).truncated


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
        owner_tasks(s, now_utc())
        anna_tasks(s, now_utc())
        make(s, "Ask Eve", people=["Eve"], now=now_utc())
        people_admin.set_person_excluded(s, "Eve", True)
        rec = add_recording(s, 1, segments=2)
        first = s.query(Segment).filter(Segment.recording_id == rec.id).order_by(Segment.id).first()
        first.speaker = "Bea <Berg>"
        s.commit()


def report_card(html):
    return html[html.index('<div class="card report">'):html.index("</div>", html.index('<div class="card report">'))]


def copy_text(html):
    start = html.index('data-copy="') + len('data-copy="')
    return html[start:html.index('"', start)]


def test_req_013_report_is_in_the_navigation_after_people(client, world):
    html = client.get("/report").text
    nav = html[html.index('<header class="top">'):html.index("</header>")]
    assert nav.index('href="/people"') < nav.index('href="/report"') < nav.index('href="/topics"')
    assert '<a class="nav current" href="/report">Report</a>' in nav


def test_req_013_the_page_shows_me_by_default_with_my_tasks_as_bullets(client, world):
    html = client.get("/report").text
    select = html[html.index('<select name="person"'):html.index("</select>")]
    assert '<option value="">Ich</option>' in select and " selected" not in select
    assert "Deine Aufgaben." in html
    card = report_card(html)
    assert "<h2>In Arbeit</h2>\n  <ul><li>Angebot schreiben</li></ul>" in card
    assert "<h2>Offen</h2>\n  <ul><li>Budget prüfen</li><li>Folien bauen</li><li>Anna anrufen</li><li>Ask Eve</li></ul>" in card
    for hidden in ("Verworfen", "Urlaub planen", "Urlaub gebucht", "Zahlen liefern"):
        assert hidden not in card


def test_req_013_picking_a_person_shows_what_they_promised(client, world):
    html = client.get("/report?person=anna").text
    assert '<option value="Anna" selected>Anna</option>' in html and "Was Anna Dir zugesagt hat." in html
    card = report_card(html)
    assert "<li>Vertrag prüfen</li>" in card and "<li>Zahlen liefern</li>" in card
    assert "Anna anrufen" not in card and "Mit Anna fertig" not in card


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
        make(s, "<b>Fett</b> & \"mehr\"", now=now_utc())
    html = client.get("/report").text
    card = report_card(html)
    assert "<li>&lt;b&gt;Fett&lt;/b&gt; &amp; &#34;mehr&#34;</li>" in card and "<a" not in card
    assert copy_text(html) == "Offen\n- &lt;b&gt;Fett&lt;/b&gt; &amp; &#34;mehr&#34;"


def test_req_013_nothing_open_says_so(client, world):
    html = client.get("/report?person=Jochen").text
    assert "<li>Bericht schicken</li>" in html
    html = client.get("/report", params={"person": "Bea <Berg>"}).text
    assert '<div class="card empty">Gerade nichts offen.</div>' in html and '<div class="card report">' not in html
    assert "data-copy" not in html


def test_req_013_a_long_page_says_that_there_are_more(client, session_factory, monkeypatch):
    monkeypatch.setattr(svc, "LIST_LIMIT_MAX", 1)
    with session_factory() as s:
        make(s, "One", now=now_utc())
        make(s, "Two", now=now_utc())
    html = client.get("/report").text
    card = report_card(html)
    assert "<li>One</li>" in card and "Two" not in card and "… und weitere" in card
    assert copy_text(html) == "Offen\n- One\n\n… und weitere"


def test_req_014_finished_tasks_come_first_under_their_own_heading(client, world):
    card = report_card(client.get("/report").text)
    assert card.index("<h2>Erledigt, letzte 7 Tage</h2>") < card.index("<h2>In Arbeit</h2>") < card.index("<h2>Offen</h2>")
    assert "<h2>Erledigt, letzte 7 Tage</h2>\n  <ul><li>Mit Anna fertig</li><li>Rechnung geprüft</li><li>Altes Thema</li></ul>" in card
    assert "Lange her" not in card
    anna = report_card(client.get("/report?person=Anna").text)
    assert "<h2>Erledigt, letzte 7 Tage</h2>\n  <ul><li>Schon geliefert</li></ul>" in anna


def test_req_014_the_copy_icon_sits_in_the_card_and_holds_the_report_as_plain_text(client, world):
    html = client.get("/report").text
    card = report_card(html)
    button = card[card.index('<button type="button" class="copy"'):card.index("</button>")]
    assert card.index("</button>") < card.index("<h2>")
    assert 'title="Kopieren" aria-label="Kopieren" hidden>' in button and '<svg class="icon-copy"' in button
    assert 'data-done="Kopiert"' in button and 'data-failed="Markiert, jetzt Strg+C"' in button
    assert '<span class="copy-note" aria-live="polite"></span>' in card
    assert copy_text(html) == ("Erledigt, letzte 7 Tage\n- Mit Anna fertig\n- Rechnung geprüft\n- Altes Thema\n\n"
                               "In Arbeit\n- Angebot schreiben\n\n"
                               "Offen\n- Budget prüfen\n- Folien bauen\n- Anna anrufen\n- Ask Eve")
    assert '<script src="/static/report.js" defer></script>' in html


def test_req_014_the_dropdown_sends_itself_and_the_button_is_only_a_fallback_without_script(client, world):
    html = client.get("/report").text
    start = html.index('<form class="searchbar" method="get" action="/report" data-report>')
    form = html[start:html.index("</form>", start)]
    assert form.count("<button") == 1 and '<noscript><button type="submit">Anzeigen</button></noscript>' in form


def test_req_014_the_script_is_served_from_this_server(client):
    script = client.get("/static/report.js")
    assert script.status_code == 200
    for part in ("form.requestSubmit()", "navigator.clipboard.writeText", "button.hidden = false"):
        assert part in script.text


def test_d_086_every_page_has_the_vault_favicon_from_this_server(client):
    page = client.get("/report")
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in page.text
    assert "img-src 'self'" in page.headers["content-security-policy"]
    icon = client.get("/static/favicon.svg")
    assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/svg+xml") and "<circle" in icon.text
