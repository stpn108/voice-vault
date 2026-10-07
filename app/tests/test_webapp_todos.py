"""Web UI for tasks, topics and overviews (REQ-006, D-013)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import todo_service as svc
import webapp
from database import Todo, TodoEvent
from utils import now_utc
from tests.helpers import add_recording


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
    def make(**kwargs):
        kwargs.setdefault("actor", "claude")
        with session_factory() as s:
            return svc.create_todo(s, now_utc(), **kwargs).id
    return make


def post(client, url, **data):
    return client.post(url, data={"csrf": webapp.csrf_token(), **data}, follow_redirects=False)


def test_req_006_list_is_sorted_by_priority_and_escapes_output(client, make_todo):
    make_todo(title="Low one", priority=4)
    make_todo(title="<script>alert(1)</script>", priority=1)
    html = client.get("/todos").text
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert html.index("alert(1)") < html.index("Low one")


def test_req_006_check_off_from_the_list_completes_and_logs_owner(client, make_todo, session_factory):
    todo_id = make_todo(title="Pay invoice")
    response = post(client, f"/todos/{todo_id}/status", status="done", back="list")
    assert response.status_code == 303 and response.headers["location"] == "/todos"
    assert "Pay invoice" not in client.get("/todos").text
    assert "Pay invoice" in client.get("/todos?status=done").text
    with session_factory() as s:
        event = s.scalars(select(TodoEvent).order_by(TodoEvent.id.desc())).first()
        assert (event.kind, event.actor) == ("completed", "owner")


def test_req_006_undo_restores_the_previous_state(client, make_todo, session_factory):
    todo_id = make_todo(title="Call Anna")
    post(client, f"/todos/{todo_id}/status", status="dropped")
    page = client.get(f"/todos/{todo_id}").text
    assert "/undo" in page
    with session_factory() as s:
        event_id = s.scalars(select(TodoEvent).order_by(TodoEvent.id.desc())).first().id
    assert post(client, f"/todos/events/{event_id}/undo").status_code == 303
    with session_factory() as s:
        assert s.get(Todo, todo_id).status == "open"
    assert post(client, f"/todos/events/{event_id}/undo").status_code == 400


def test_req_006_owner_adds_and_edits_a_task(client, session_factory):
    response = post(client, "/todos", title="Buy stamps", priority="2", due="2026-12-01", topic="Errands")
    todo_id = int(response.headers["location"].rsplit("/", 1)[1])
    post(client, f"/todos/{todo_id}", title="Buy stamps", detail="Post office", priority="1", due="",
         topic="Errands", status="open")
    with session_factory() as s:
        todo = s.get(Todo, todo_id)
        assert (todo.priority, todo.detail, todo.due_date, todo.created_by) == (1, "Post office", None, "owner")


@pytest.mark.parametrize("url,data", [
    ("/todos", {"title": "x"}), ("/todos/1", {"title": "x"}), ("/todos/1/status", {"status": "done"}),
    ("/todos/events/1/undo", {}),
])
def test_req_006_state_changes_need_the_csrf_token(client, make_todo, url, data):
    make_todo(title="Keep me")
    assert client.post(url, data={"csrf": "wrong", **data}).status_code == 403
    assert client.post(url, data=data).status_code == 403


@pytest.mark.parametrize("url,data", [
    ("/todos", {"title": ""}), ("/todos", {"title": "x", "priority": "9"}), ("/todos", {"title": "x", "due": "soon"}),
    ("/todos/1/status", {"status": "deleted"}),
])
def test_req_006_invalid_input_is_rejected(client, make_todo, url, data):
    make_todo(title="Keep me")
    assert post(client, url, **data).status_code in (400, 422)


@pytest.mark.parametrize("url", ["/todos/999", "/topics/Nothing"])
def test_req_006_unknown_ids_are_404(client, url):
    assert client.get(url).status_code == 404


def test_req_006_topics_timeline_and_digests_render(client, make_todo, session_factory):
    with session_factory() as s:
        rec_id = add_recording(s, index=1).id
    make_todo(title="Prepare budget", topic="Budget", recording_id=rec_id)
    with session_factory() as s:
        svc.add_topic_note(s, now_utc(), topic="Budget", recording_id=rec_id, note="Estimate 12k")
        svc.save_digest(s, now_utc(), dt.date(2026, 10, 5), "Today: <b>budget</b>")
    assert "Budget" in client.get("/topics").text
    topic = client.get("/topics/budget").text
    assert "Estimate 12k" in topic and "Prepare budget" in topic
    digests = client.get("/digests").text
    assert "05.10.2026" in digests and "&lt;b&gt;budget" in digests


def test_req_006_empty_pages_render(client):
    for url in ("/todos", "/topics", "/digests"):
        assert client.get(url).status_code == 200
