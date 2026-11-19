"""Web UI of the task board, the table and the home page (REQ-008, D-074)."""
import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import people_admin
import task_links
import todo_service as svc
import webapp
from database import TodoEvent
from utils import now_utc


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    webapp.app.dependency_overrides[webapp.get_session] = override
    yield TestClient(webapp.app, base_url="http://localhost")
    webapp.app.dependency_overrides.clear()


@pytest.fixture
def make_todo(session_factory):
    def make(status=None, at=None, **kwargs):
        kwargs.setdefault("actor", "claude")
        with session_factory() as s:
            todo_id = svc.create_todo(s, at or now_utc(), allow_similar=True, **kwargs).id
            if status:
                svc.update_todo(s, at or now_utc(), todo_id, actor="owner", status=status)
            return todo_id
    return make


def cell(html, lane, status):
    """The markup of one cell of the board."""
    row = html[html.index(f'data-lane="{lane}"'):]
    start = row.index(f'<div class="cell" data-status="{status}"')
    return row[start:row.index('<div class="cell"', start + 1) if status != "done" else row.index("</details>", start)]


def card(html, title):
    start = html.rindex("<article", 0, html.index(title + "</a>", html.index("data-board")))
    return html[start:html.index("</article>", start)]


def test_req_008_tasks_are_the_home_page_and_recordings_are_one_click_away(client, make_todo):
    make_todo(title="Write offer")
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/todos"
    home = client.get("/").text
    assert "data-board" in home and "Write offer" in home
    nav = home[home.index('<header class="top">'):home.index("</header>")]
    order = [nav.index(f'href="{path}"') for path in ("/todos", "/topics", "/digests", "/stats", "/recordings")]
    assert order == sorted(order) and 'class="brand" href="/"' in nav
    recordings = client.get("/recordings")
    assert recordings.status_code == 200 and 'action="/recordings"' in recordings.text and "data-board" not in recordings.text


def test_req_008_columns_are_the_status_and_rows_the_priority(client, make_todo):
    make_todo(title="Urgent open", priority=1)
    make_todo(title="Urgent started", priority=1, status="in_progress")
    make_todo(title="Normal done", priority=3, status="done")
    make_todo(title="Done long ago", priority=3, status="done", at=now_utc() - dt.timedelta(days=8))
    make_todo(title="Dropped", status="dropped")
    make_todo(title="Promised", kind="waiting", people=["Anna"])
    html = client.get("/todos").text
    assert "Urgent open" in cell(html, "p1", "open") and "Urgent started" in cell(html, "p1", "in_progress")
    assert "Normal done" in cell(html, "p3", "done")
    assert not any(title in html[html.index("data-board"):] for title in ("Done long ago", "Dropped", "Promised"))
    assert [html.index(f'data-lane="p{p}"') for p in (1, 2, 3, 4)] == sorted(html.index(f'data-lane="p{p}"') for p in (1, 2, 3, 4))
    for status, number in (("open", 1), ("in_progress", 1), ("done", 1)):
        assert f'<b data-col="{status}">{number}</b>' in html
    assert "letzte 7 Tage" in html and 'href="/todos?status=done"' in html
    urgent = html[html.index('data-lane="p1"'):html.index('data-lane="p2"')]
    assert '<span data-n="open">1</span> offen' in urgent and '<span data-n="in_progress">1</span> in Arbeit' in urgent


def test_req_008_rows_fold_and_low_starts_folded(client, make_todo):
    make_todo(title="Some day", priority=4)
    html = client.get("/todos").text
    lanes = dict(re.findall(r'<details class="lane" data-lane="(p\d)" (open)?', html))
    assert lanes == {"p1": "open", "p2": "open", "p3": "open", "p4": ""}
    assert "Some day" in cell(html, "p4", "open")  # folded, not left out


def test_req_008_rows_by_topic_or_none(client, make_todo):
    make_todo(title="Book hotel", topic="Messe Frankfurt")
    make_todo(title="Check numbers", topic="Budget 2027")
    make_todo(title="Call back")
    by_topic = client.get("/todos?group=topic").text
    assert by_topic.index("<strong>Budget 2027</strong>") < by_topic.index("<strong>Messe Frankfurt</strong>") < by_topic.index("<strong>Ohne Thema</strong>")
    assert ">Budget 2027</a>" not in card(by_topic, "Check numbers")  # the row names the topic already
    flat = client.get("/todos?group=none").text
    assert "<details" not in flat[flat.index("data-board"):flat.index('id="toast"')] and flat.count("<article") == 3
    assert ">Budget 2027</a>" in card(flat, "Check numbers")


def test_req_008_a_card_shows_what_matters(client, make_todo, session_factory):
    late = now_utc().astimezone(webapp.LOCAL_TZ).date() - dt.timedelta(days=3)
    old = now_utc() - dt.timedelta(days=12)
    contract = make_todo(title="Vertragsentwurf an Anwalt weiterleiten", priority=1, due=late.isoformat(), topic="Mietvertrag",
                         people=["Miriam Vogt", "Dennis Winter"], at=old)
    other = make_todo(title="Übergabeprotokoll abstimmen", at=old)
    fresh = make_todo(title="Rückruf Steuerberater")
    with session_factory() as s:
        task_links.link(s, now_utc(), contract, other)
        people_admin.set_person_excluded(s, "Dennis Winter", True)
    html = client.get("/todos").text
    shown = card(html, "Vertragsentwurf an Anwalt weiterleiten")
    assert f'href="/todos/{contract}"' in shown and '<span class="dot p1" title="Dringend">' in shown
    assert ">Mietvertrag</a>" in shown and f'class="due overdue">Überfällig {late.strftime("%d.%m.")}' in shown
    assert f'data-links="{other}"' in shown and '<button type="button" class="link-pin" aria-pressed="false"' in shown
    assert 'title="Verknüpft mit: Übergabeprotokoll abstimmen. Ein Klick hält die Linien fest.">🔗 1</button>' in shown
    assert 'title="Miriam Vogt">MV</a>' in shown and "Dennis Winter" not in shown  # excluded people stay out (D-055)
    assert 'class="pill new"' not in shown and "12 Tage" not in shown  # the due date takes the place of the age
    assert "12 Tage" in card(html, "Übergabeprotokoll abstimmen")
    assert 'class="pill new">neu<' in card(html, "Rückruf Steuerberater") and f'data-id="{fresh}"' in html


def test_req_008_a_card_marks_an_unclear_name(client, make_todo, session_factory):
    import glossary_admin
    with session_factory() as s:
        glossary_admin.add_term(s, "Stefan Zimmermann", "", "Stefan")
        glossary_admin.add_term(s, "Stephan Simmang", "", "Stefan")
    make_todo(title="Mit Stefan reden")
    assert ">Stefan?</span>" in card(client.get("/todos").text, "Mit Stefan reden")


@pytest.mark.parametrize("status, check, offered, hidden_by", [
    ("open", "done", "in_progress", 'data-status="open"'),
    ("in_progress", "done", "open", 'data-status="in_progress"'),
    ("done", "open", "", 'data-status="done"'),
])
def test_req_008_a_card_moves_with_its_buttons_without_a_script(client, make_todo, status, check, offered, hidden_by):
    todo = make_todo(title="Write offer", status=None if status == "open" else status)
    shown = card(client.get("/todos").text, "Write offer")
    assert hidden_by in shown and shown.count(f'method="post" action="/todos/{todo}/status"') == 2
    assert f'<button class="check" type="submit" name="status" value="{check}"' in shown
    for value in ("in_progress", "open", "dropped"):
        assert f'name="status" value="{value}"' in shown  # all rendered; the stylesheet shows the ones that fit the status
    if offered:
        response = client.post(f"/todos/{todo}/status", data={"csrf": webapp.csrf_token(), "status": offered, "back": "page"},
                               headers={"referer": "http://localhost/todos?group=topic"}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "http://localhost/todos?group=topic"


def test_req_008_a_drag_into_another_row_is_one_change_and_one_undo(client, make_todo, session_factory):
    todo = make_todo(title="Write offer", priority=3)
    body = client.post(f"/todos/{todo}/status", data={"csrf": webapp.csrf_token(), "status": "in_progress", "priority": "1"},
                       headers={"Accept": "application/json"}).json()
    assert (body["status"], body["previous"], body["previous_priority"]) == ("in_progress", "open", 3)
    assert body["counts"]["in_progress"] == 1 and body["wip"] == 1 and body["in_progress"][0]["id"] == todo
    with session_factory() as s:
        events = s.scalars(select(TodoEvent).where(TodoEvent.todo_id == todo).order_by(TodoEvent.id)).all()
        assert [e.kind for e in events] == ["created", "started"] and events[-1].actor == "owner"
        row = svc.get_todo(s, now_utc(), todo)[0]
        assert (row.status, row.priority) == ("in_progress", 1)
        svc.undo_event(s, now_utc(), events[-1].id)
        row = svc.get_todo(s, now_utc(), todo)[0]
        assert (row.status, row.priority) == ("open", 3)
    assert "Write offer" in cell(client.get("/todos").text, "p3", "open")


def test_req_008_a_drag_within_a_column_changes_only_the_priority(client, make_todo, session_factory):
    todo = make_todo(title="Write offer", priority=3)
    client.post(f"/todos/{todo}/status", data={"csrf": webapp.csrf_token(), "status": "open", "priority": "2"},
                headers={"Accept": "application/json"})
    with session_factory() as s:
        row, events = svc.get_todo(s, now_utc(), todo)
        assert (row.status, row.priority, events[0].kind) == ("open", 2, "updated")
    bad = client.post(f"/todos/{todo}/status", data={"csrf": webapp.csrf_token(), "status": "open", "priority": "9"})
    assert bad.status_code == 400


def test_req_008_quick_filters_show_their_counts_and_narrow_the_board(client, make_todo):
    today = now_utc().astimezone(webapp.LOCAL_TZ).date()
    old = now_utc() - dt.timedelta(days=10)
    make_todo(title="Late one", due=(today - dt.timedelta(days=1)).isoformat(), at=old)
    make_todo(title="Soon one", due=(today + dt.timedelta(days=2)).isoformat(), at=old)
    make_todo(title="Fresh one")
    html = client.get("/todos").text
    for name, label in (("overdue", "Überfällig"), ("soon", "Diese Woche fällig"), ("new", "Neu")):
        assert f'href="/todos?group=priority&amp;only={name}">{label} <b>1</b>' in html
    late = client.get("/todos?only=overdue").text
    assert "Late one" in late and "Soon one" not in late and "Fresh one" not in late
    assert 'class="chip only-overdue current" href="/todos?group=priority"' in late  # a second click clears the filter
    assert "Soon one" in client.get("/todos?only=soon").text and "Fresh one" in client.get("/todos?only=new").text


def test_req_008_topic_and_person_narrow_the_board(client, make_todo):
    make_todo(title="Budget with Anna", topic="Budget", people=["Anna"])
    make_todo(title="Call back")
    by_topic = client.get("/todos?topic=Budget").text
    assert "Budget with Anna" in by_topic and "Call back" not in by_topic and "Thema: <strong>Budget</strong>" in by_topic
    assert 'href="/todos?group=priority&amp;topic=Budget"' in client.get("/todos").text
    by_person = client.get("/todos?person=Anna").text
    assert "Budget with Anna" in by_person and "Call back" not in by_person


@pytest.mark.parametrize("query", ["group=colour", "only=nonsense", "view=cards"])
def test_req_008_unknown_view_grouping_or_filter_is_a_400(client, query):
    assert client.get(f"/todos?{query}").status_code == 400


def test_req_008_the_board_loads_its_script_from_this_server_only(client, make_todo):
    make_todo(title="Write offer")
    html = client.get("/todos").text
    assert '<script src="/static/board.js" defer></script>' in html and "todos.js" not in html and "<script>" not in html
    assert 'id="toast"' in html and 'data-count="open"' in html and 'draggable="true"' in html
    script = client.get("/static/board.js")
    assert script.status_code == 200 and "/status" in script.text and "todo:changed" in script.text
    assert "link-label" in script.text and "link-label" in html  # labels for linked cards that are not on screen (D-075)


def test_req_008_the_table_is_the_list_with_one_line_per_task(client, make_todo):
    a = make_todo(title="Write offer", priority=1, topic="Budget", people=["Anna"])
    b = make_todo(title="Call back", priority=3, status="in_progress")
    make_todo(title="Done one", status="done")
    page = client.get("/todos?view=table").text
    html = page.split("</style>")[1]
    assert '<ul class="list dense">' in html and html.count("<ul class=\"list") == 1 and "group-title" not in html
    assert html.count('<li class="task') == 2 and "Done one" not in html and "data-board" not in html
    head = html[html.index('<li class="dense-head">'):html.index("</li>", html.index('<li class="dense-head">'))]
    for label in ("Aufgabe", "Status", "Thema", "Personen", "Fällig", "Alter", "Priorität"):
        assert f">{label}<" in head
    assert 'href="/todos?sort=due&amp;view=table"' in head
    assert f'data-id="{a}"' in html and f'data-id="{b}"' in html and 'data-status="in_progress"' in html
    for needed in ('class="link-handle"', "data-quick", "/static/todos.js", "/static/links.js", 'value="in_progress"', 'value="dropped"'):
        assert needed in html
    by_title = client.get("/todos?view=table&sort=title").text
    assert by_title.index("Call back") < by_title.index("Write offer")
    assert '<body class="wide">' in html and '<body class="wide">' in client.get("/todos").text
    assert "list.dense" in page  # the stylesheet lays the rows out as one line


def test_req_008_board_and_table_switch_and_the_other_lists_stay_lists(client, make_todo):
    make_todo(title="Write offer")
    make_todo(title="Finished", status="done")
    make_todo(title="Change ticket", kind="waiting", people=["Jochen Rill"])
    board = client.get("/todos").text
    assert '<a href="/todos?view=board" class="current">Board</a>' in board and '<a href="/todos?view=table" >Tabelle</a>' in board
    for url in ("/todos?status=done", "/todos?status=in_progress", "/todos?status=dropped", "/todos?kind=waiting",
                "/todos?kind=waiting&view=table", "/todos?status=done&view=table"):
        html = client.get(url).text
        assert "data-board" not in html and "dense" not in html.split("</style>")[1] and "?view=board" not in html
        assert '<body class="">' in html
    assert "Finished" in client.get("/todos?status=done").text and "Change ticket" in client.get("/todos?kind=waiting").text
