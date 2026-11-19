"""The dashboard of all people and the search for a person (REQ-010, D-077)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

import briefing_service as brief
import people_admin
import person_service
import todo_service as svc
import webapp
from database import Segment
from tests.helpers import add_recording
from utils import now_utc

NOW = dt.datetime(2026, 11, 19, 9, 0, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 11, 19)


def make(session, title, status=None, **kw):
    todo = svc.create_todo(session, NOW, title=title, actor="owner", allow_similar=True, **kw)
    if status:
        svc.update_todo(session, NOW, todo.id, actor="owner", status=status)
    return todo.id


def talk(session, index, who, days_ago, now=NOW):
    """A recording in which `who` spoke, next to an unnamed speaker."""
    rec = add_recording(session, index, started=now - dt.timedelta(days=days_ago), segments=2)
    first = session.query(Segment).filter(Segment.recording_id == rec.id).order_by(Segment.id).first()
    first.speaker = who
    session.commit()
    return rec


def by_name(cards):
    return {card.name: card for card in cards}


def test_req_010_one_card_per_known_person_without_excluded_and_unnamed(db_session):
    make(db_session, "Write offer", people=["Anna", "Jochen Rill"])
    make(db_session, "Ask Eve", people=["Eve"])
    talk(db_session, 1, "Jochen Rill", 2)
    people_admin.set_person_excluded(db_session, "Eve", True)
    assert sorted(by_name(brief.overview(db_session, NOW, TODAY))) == ["Anna", "Jochen Rill"]


def test_req_010_a_card_counts_tasks_by_status_promises_and_what_is_overdue(db_session):
    make(db_session, "Open one", people=["Anna"])
    make(db_session, "Open and late", people=["Anna"], due="2026-11-18")
    make(db_session, "Started", people=["Anna", "Jochen Rill"], status="in_progress")
    make(db_session, "Done", people=["Anna"], status="done")
    make(db_session, "Dropped", people=["Anna"], status="dropped")
    make(db_session, "Promised and late", kind="waiting", people=["Anna"], due="2026-11-01")
    make(db_session, "Promised, due today", kind="waiting", people=["Anna"], due="2026-11-19")
    make(db_session, "Family", people=["Anna"], topic="Private")
    svc.set_topic_excluded(db_session, "Private", True)
    cards = by_name(brief.overview(db_session, NOW, TODAY))
    anna, jochen = cards["Anna"], cards["Jochen Rill"]
    assert (anna.open, anna.in_progress, anna.waiting, anna.overdue) == (2, 1, 2, 2)
    assert (jochen.open, jochen.in_progress, jochen.waiting, jochen.overdue) == (0, 1, 0, 0)
    # the same numbers the person's page works with
    page = brief.briefing(db_session, NOW, "Anna")
    assert len(page.mine) == anna.open + anna.in_progress and len(page.waiting) == anna.waiting


def test_req_010_a_card_knows_the_conversations_the_profile_and_the_agreements(db_session):
    make(db_session, "Write offer", people=["Anna"])
    talk(db_session, 1, "Anna", 9)
    talk(db_session, 2, "anna", 3)
    discarded = talk(db_session, 3, "Anna", 0)
    discarded.discarded_at = NOW
    db_session.commit()
    person_service.update_profile(db_session, NOW, "Anna", actor="claude", role="CFO", next_points="Budget ansprechen",
                                  agreements=["Punkt 1", "Punkt 2"])
    card = by_name(brief.overview(db_session, NOW, TODAY))["Anna"]
    assert (card.conversations, card.days_since, card.last_talk) == (2, 3, NOW - dt.timedelta(days=3))
    assert (card.role, card.next_points, card.agreements) == ("CFO", "Budget ansprechen", ["Punkt 1", "Punkt 2"])
    page = brief.briefing(db_session, NOW, "Anna")
    assert (page.conversations, page.last_talk) == (card.conversations, card.last_talk)


@pytest.mark.parametrize("sort, expected", [
    ("recent", ["Bea", "Anna", "Carl", "Dora"]),   # last conversation first, without one last and by name
    ("open", ["Carl", "Dora", "Anna", "Bea"]),     # most open points first
    ("name", ["Anna", "Bea", "Carl", "Dora"]),
])
def test_req_010_cards_are_sorted_by_last_conversation_open_points_or_name(db_session, sort, expected):
    make(db_session, "One", people=["Anna", "Bea", "Carl", "Dora"])
    make(db_session, "Two", people=["Carl", "Dora"])
    make(db_session, "Three", kind="waiting", people=["Carl"])
    talk(db_session, 1, "Anna", 5)
    talk(db_session, 2, "Bea", 1)
    assert [card.name for card in brief.overview(db_session, NOW, TODAY, sort)] == expected
    with pytest.raises(ValueError):
        brief.overview(db_session, NOW, TODAY, "nonsense")


@pytest.mark.parametrize("q, expected", [("ann", ["Anna Becker", "Joanna Wolf"]), ("  BECKER ", ["Anna Becker"]),
                                         ("xyz", []), ("", ["Anna Becker", "Joanna Wolf", "Sven Arnold"])])
def test_req_010_the_search_keeps_the_people_whose_name_contains_the_text(db_session, q, expected):
    make(db_session, "One", people=["Anna Becker", "Joanna Wolf", "Sven Arnold"])
    assert [card.name for card in brief.overview(db_session, NOW, TODAY, "name", q)] == expected


# --- the page -------------------------------------------------------------------------------------------
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
    yesterday = (now_utc().astimezone(webapp.LOCAL_TZ).date() - dt.timedelta(days=1)).isoformat()
    with session_factory() as s:
        for n in range(3):
            svc.create_todo(s, now_utc(), title=f"Task {n}", actor="owner", people=["Anna Becker"], allow_similar=True)
        started = svc.create_todo(s, now_utc(), title="Started", actor="owner", people=["Anna Becker", "Sven <Arnold>"])
        svc.update_todo(s, now_utc(), started.id, actor="owner", status="in_progress")
        svc.create_todo(s, now_utc(), title="Promised", actor="owner", kind="waiting", people=["Anna Becker"], due=yesterday)
        talk(s, 1, "Anna Becker", 1, now=now_utc())
        person_service.update_profile(s, now_utc(), "Anna Becker", actor="claude", role="Leitung Marketing",
                                      next_points="Fotos ansprechen", agreements=[f"Absprache {n}" for n in range(1, 6)])


def test_req_010_people_are_in_the_navigation_and_each_has_a_card(client, world):
    html = client.get("/people").text
    nav = html[html.index('<header class="top">'):html.index("</header>")]
    assert nav.index('href="/todos"') < nav.index('href="/people"') < nav.index('href="/topics"')
    assert '<a class="nav current" href="/people">Personen</a>' in nav
    assert html.count('<article class="card pcard">') == 2 and '<body class="wide">' in html
    card = html[html.index('<article class="card pcard">'):html.index("</article>")]
    assert '<a href="/person?name=Anna%20Becker&amp;from=people">Anna Becker</a><span class="muted">Leitung Marketing</span>' in card
    assert "Gestern gesprochen · 1 Gespräch" in card
    for number, label in ((3, "Offen"), (1, "In Arbeit"), (1, "Schuldet Dir"), (1, "überfällig")):
        assert f"<b>{number}</b>{label}<" in card
    assert 'href="/todos?person=Anna%20Becker"' in card and 'href="/todos?kind=waiting&amp;person=Anna%20Becker"' in card
    assert "Fotos ansprechen" in card
    assert "<li>Absprache 1</li><li>Absprache 2</li><li>Absprache 3</li></ul>" in card and "Absprache 4" not in card
    assert ">+2 weitere</a>" in card
    assert "Sven &lt;Arnold&gt;" in html and "<Arnold>" not in html and "Noch keine Aufnahme" in html
    assert 'href="/todos?group=person"' in html


def test_req_010_the_person_page_opened_from_the_dashboard_leads_back_there(client, world):
    page = client.get("/person?name=Anna%20Becker&from=people").text
    crumbs = page[page.index('<nav class="crumbs"'):page.index("</nav>", page.index('<nav class="crumbs"'))]
    assert '<a href="/people">Personen</a>' in crumbs and 'href="/todos"' not in crumbs
    assert '<a class="nav current" href="/people">' in page
    plain = client.get("/person?name=Anna%20Becker").text
    assert '<a href="/todos">Aufgaben</a>' in plain[plain.index('<nav class="crumbs"'):] and '<a class="nav current" href="/todos">' in plain


def test_req_010_the_cards_can_be_sorted_and_an_unknown_sort_is_a_400(client, world):
    by_name = client.get("/people?sort=name").text
    assert by_name.index(">Anna Becker</a>") < by_name.index(">Sven &lt;Arnold&gt;</a>")
    assert '<a class="chip current" href="/people?sort=name">Name</a>' in by_name
    assert client.get("/people?sort=nonsense").status_code == 400


def test_req_010_a_search_narrows_the_cards_and_one_hit_opens_the_person(client, world):
    html = client.get("/people").text
    assert '<form class="searchbar" method="get" action="/people">' in html and '<option value="Anna Becker">' in html
    several = client.get("/people?q=e", follow_redirects=False)
    assert several.status_code == 200 and several.text.count('<article class="card pcard">') == 2
    assert 'value="e"' in several.text and 'href="/people?sort=name&amp;q=e"' in several.text
    one = client.get("/people?q=becker", follow_redirects=False)
    assert one.status_code == 303 and one.headers["location"] == "/person?name=Anna%20Becker&from=people"
    none = client.get("/people?q=%3Cnobody%3E")
    assert none.status_code == 200 and "Keine Person mit „&lt;nobody&gt;“ im Namen." in none.text and "<nobody>" not in none.text


def test_req_010_without_people_the_page_says_so(client):
    assert "Noch keine Personen." in client.get("/people").text
