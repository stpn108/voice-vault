"""Owner-only topic deletion and task sorting (REQ-006, D-017)."""
import datetime as dt
import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mcp_todo_tools
import todo_service as svc
import topic_admin
import webapp
from database import Todo, TodoEvent, Topic, TopicNote
from tests.helpers import add_recording
from todo_service import TodoError

NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone.utc)


def make(session, title, **kwargs):
    kwargs.setdefault("actor", "claude")
    return svc.create_todo(session, NOW, title=title, **kwargs)


# --- deleting a topic ---------------------------------------------------------------------------
def test_req_006_deleting_a_topic_keeps_its_tasks_and_logs_the_change(db_session):
    rec = add_recording(db_session, 1)
    task = make(db_session, "Plan trip", topic="Travel")
    svc.add_topic_note(db_session, NOW, topic="Travel", recording_id=rec.id, note="Vienna in May")
    result = topic_admin.delete_topic(db_session, NOW, "travel")
    assert result == {"name": "Travel", "tasks": 1, "notes": 1, "excluded": False}
    assert db_session.scalar(select(Topic).where(Topic.name == "Travel")) is None
    assert db_session.scalars(select(TopicNote)).all() == []
    db_session.refresh(task)
    assert task.topic_id is None and task.status == "open"
    event = db_session.scalars(select(TodoEvent).where(TodoEvent.todo_id == task.id).order_by(TodoEvent.id.desc())).first()
    assert (event.kind, event.actor, event.note) == ("updated", "owner", "topic deleted")


def test_req_006_the_topic_removal_of_a_task_can_be_undone(db_session):
    task = make(db_session, "Plan trip", topic="Travel")
    topic_admin.delete_topic(db_session, NOW, "Travel")
    event = db_session.scalars(select(TodoEvent).where(TodoEvent.todo_id == task.id).order_by(TodoEvent.id.desc())).first()
    svc.undo_event(db_session, NOW, event.id)
    assert svc.list_todos(db_session, NOW, topic="Travel")[0][0].title == "Plan trip"


def test_req_006_delete_with_exclusion_keeps_a_blocking_topic(db_session):
    make(db_session, "Plan trip", topic="Travel")
    assert topic_admin.delete_topic(db_session, NOW, "Travel", keep_excluded=True)["excluded"] is True
    with pytest.raises(TodoError, match="excluded"):
        make(db_session, "Book hotel", topic="Travel")


def test_req_006_deleting_an_unknown_topic_does_nothing(db_session):
    assert topic_admin.delete_topic(db_session, NOW, "Nothing") is None


def test_req_006_claude_has_no_path_to_topic_deletion():
    assert "topic_admin" not in inspect.getsource(mcp_todo_tools)
    assert "topic_admin" not in inspect.getsource(svc)
    assert not [t["name"] for t in mcp_todo_tools.TOOLS if "topic" in t["name"] and "delete" in t["name"]]


# --- sorting -------------------------------------------------------------------------------------
@pytest.fixture
def sorted_tasks(db_session):
    first = make(db_session, "banana", priority=3, due="2026-10-20", topic="Zeta")
    second = svc.create_todo(db_session, NOW + dt.timedelta(hours=1), title="Apple", actor="owner", priority=1,
                             topic="alpha")
    third = svc.create_todo(db_session, NOW + dt.timedelta(hours=2), title="cherry", actor="owner", priority=3,
                            due="2026-10-10")
    return first, second, third


@pytest.mark.parametrize("sort,expected", [
    ("priority", ["Apple", "cherry", "banana"]),
    ("due", ["cherry", "banana", "Apple"]),
    ("newest", ["cherry", "Apple", "banana"]),
    ("oldest", ["banana", "Apple", "cherry"]),
    ("topic", ["Apple", "banana", "cherry"]),
    ("title", ["Apple", "banana", "cherry"]),
])
def test_req_006_tasks_can_be_sorted(db_session, sorted_tasks, sort, expected):
    assert [r.title for r in svc.list_todos(db_session, NOW, sort=sort)[0]] == expected


def test_req_006_an_unknown_sort_is_rejected(db_session):
    with pytest.raises(TodoError, match="sort must be one of"):
        svc.list_todos(db_session, NOW, sort="random")


# --- the pages ------------------------------------------------------------------------------------
@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    webapp.app.dependency_overrides[webapp.get_session] = override
    yield TestClient(webapp.app, base_url="http://localhost")
    webapp.app.dependency_overrides.clear()


def post(client, url, **data):
    return client.post(url, data={"csrf": webapp.csrf_token(), **data}, follow_redirects=False)


def test_req_006_the_confirm_page_says_what_goes_and_what_stays(client, session_factory):
    with session_factory() as s:
        make(s, "Plan trip", topic="Travel")
        make(s, "Book hotel", topic="Travel")
    page = client.get("/topics/Travel/delete").text
    assert "Thema löschen?" in page and "2 Aufgabe(n), davon 2 offen" in page and 'name="exclude"' in page
    assert client.get("/topics/Nothing/delete").status_code == 404


def test_req_006_deleting_through_the_ui_needs_the_csrf_token_and_confirmation(client, session_factory):
    with session_factory() as s:
        make(s, "Plan trip", topic="Travel")
    assert client.post("/topics/delete", data={"name": "Travel", "csrf": "bad"}).status_code == 403
    assert client.get("/topics/Travel").status_code == 200
    assert post(client, "/topics/delete", name="Travel", exclude="1").status_code == 303
    page = client.get("/topics").text
    assert "Travel" in page and "ausgeschlossen" in page
    assert post(client, "/topics/delete", name="Travel").status_code == 303
    assert client.get("/topics/Travel").status_code == 404
    assert post(client, "/topics/delete", name="Travel").status_code == 404
    with session_factory() as s:
        assert s.scalars(select(Todo)).one().topic_id is None


def test_req_006_the_list_can_be_sorted_and_flattens_when_not_by_priority(client, session_factory):
    with session_factory() as s:
        make(s, "banana", priority=3)
        make(s, "Apple", priority=1)
    by_priority = client.get("/todos").text
    assert "<h2 class=\"group-title\"" in by_priority
    by_title = client.get("/todos?sort=title").text
    assert "<h2 class=\"group-title\"" not in by_title and by_title.index("Apple") < by_title.index("banana")
    assert client.get("/todos?sort=nonsense").status_code == 400
