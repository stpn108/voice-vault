"""Web UI for tasks, topics and overviews (REQ-006, D-013)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import todo_service as svc
import webapp
from database import Todo, TodoEvent
from utils import now_utc
from tests.helpers import add_recording, add_recording_on


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


@pytest.mark.parametrize("url", ["/todos/999", "/topics/999"])
def test_req_006_unknown_ids_are_404(client, url):
    assert client.get(url).status_code == 404


def test_req_006_topics_timeline_and_digests_render(client, make_todo, session_factory):
    with session_factory() as s:
        rec_id = add_recording(s, index=1).id
        add_recording_on(s, dt.date(2026, 10, 5))
    make_todo(title="Prepare budget", topic="Budget", recording_id=rec_id)
    with session_factory() as s:
        svc.add_topic_note(s, now_utc(), topic="Budget", recording_id=rec_id, note="Estimate 12k")
        svc.save_digest(s, now_utc(), dt.date(2026, 10, 5), "Today: <b>budget</b>")
    assert "Budget" in client.get("/topics").text
    from database import Topic
    with session_factory() as s:
        budget_id = s.scalar(select(Topic.id).where(Topic.name == "Budget"))
    topic = client.get(f"/topics/{budget_id}").text
    assert "Estimate 12k" in topic and "Prepare budget" in topic
    digests = client.get("/digests").text
    assert "05.10.2026" in digests and "&lt;b&gt;budget" in digests


def test_req_006_empty_pages_render(client):
    for url in ("/todos", "/topics", "/digests"):
        assert client.get(url).status_code == 200


def test_req_006_owner_excludes_and_allows_a_topic(client, make_todo, session_factory):
    make_todo(title="Family trip", topic="Family")
    assert post(client, "/topics/exclude", name="Family", excluded="1").status_code == 303
    assert "Family trip" not in client.get("/todos").text
    assert "ausgeschlossen" in client.get("/topics").text
    assert post(client, "/topics/exclude", name="NotYetThere", excluded="1").status_code == 303
    assert post(client, "/topics/exclude", name="Family", excluded="0").status_code == 303
    assert "Family trip" in client.get("/todos").text


def test_req_006_excluding_needs_the_csrf_token(client):
    assert client.post("/topics/exclude", data={"name": "X", "csrf": "bad"}).status_code == 403


def test_req_006_digests_can_be_paged_through_by_day(client, session_factory):
    with session_factory() as s:
        add_recording_on(s, "2026-10-01", "2026-10-03", "2026-10-07")
        for day, body in ((dt.date(2026, 10, 1), "first day"), (dt.date(2026, 10, 3), "third day"),
                          (dt.date(2026, 10, 7), "last day")):
            svc.save_digest(s, now_utc(), day, body)
    latest = client.get("/digests").text
    assert "last day" in latest and 'href="/digests/2026-10-03"' in latest and 'rel="next"' in latest
    assert 'href="/digests/2026-10-07"' not in latest.split('rel="next"')[0].rsplit("<a", 1)[-1]
    middle = client.get("/digests/2026-10-03").text
    assert "third day" in middle and 'href="/digests/2026-10-01"' in middle and 'href="/digests/2026-10-07"' in middle
    assert "first day" in client.get("/digests/2026-10-01").text
    assert "Title 100" in client.get("/digests/2026-10-01").text


@pytest.mark.parametrize("day", ["2026-10-02", "nonsense", "2026-13-40"])
def test_req_006_unknown_digest_days_are_404(client, day):
    assert client.get(f"/digests/{day}").status_code == 404
