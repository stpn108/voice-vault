"""MCP task tools (REQ-006, D-013): writes are attributed to claude, logged, never destructive."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mcp_server
from database import Recording, Todo, TodoEvent
from tests.helpers import add_recording
from tests.mcp_helpers import call, make_cfg, text_of


@pytest.fixture
def client(session_factory):
    return TestClient(mcp_server.create_app(make_cfg(), session_factory), base_url="http://localhost")


@pytest.fixture
def rec_id(session_factory):
    with session_factory() as s:
        return add_recording(s, index=1).id


def is_error(response):
    return response.json()["result"]["isError"]


def add(client, **arguments):
    response = call(client, "add_todo", arguments)
    assert not is_error(response), text_of(response)
    return int(text_of(response).split("#")[1].split(":")[0])


def test_req_006_add_todo_creates_logged_task_by_claude(client, session_factory, rec_id):
    todo_id = add(client, title="Send offer", priority=1, due="2026-11-01", topic="Sales", recording_id=rec_id)
    with session_factory() as s:
        todo = s.get(Todo, todo_id)
        assert (todo.priority, todo.created_by, todo.status) == (1, "claude", "open")
        event = s.scalar(select(TodoEvent).where(TodoEvent.todo_id == todo_id))
        assert (event.kind, event.actor, event.recording_id) == ("created", "claude", rec_id)
    listing = text_of(call(client, "list_todos"))
    assert "#%d | P1 | open | Send offer | due 2026-11-01 | topic Sales" % todo_id in listing


def test_req_006_similar_task_is_rejected_unless_allowed(client):
    add(client, title="Call the tax advisor about invoice")
    second = call(client, "add_todo", {"title": "Call the tax advisor about the invoice"})
    assert is_error(second) and "similar open task" in text_of(second)
    assert not is_error(call(client, "add_todo", {"title": "Call the tax advisor about invoice",
                                                  "allow_similar": True}))


def test_req_006_update_completes_and_reopens_and_clears_fields(client, session_factory):
    todo_id = add(client, title="Book flight", due="2026-11-02", topic="Travel")
    assert not is_error(call(client, "update_todo", {"id": todo_id, "status": "done", "note": "owner said so"}))
    assert "No open tasks" in text_of(call(client, "list_todos"))
    assert "Book flight" in text_of(call(client, "list_todos", {"status": "done"}))
    call(client, "update_todo", {"id": todo_id, "status": "open", "due": "", "topic": ""})
    with session_factory() as s:
        todo = s.get(Todo, todo_id)
        assert (todo.status, todo.due_date, todo.topic_id) == ("open", None, None)
        kinds = [e.kind for e in s.scalars(select(TodoEvent).order_by(TodoEvent.id))]
        assert kinds == ["created", "completed", "reopened"]


def test_req_006_update_without_change_and_unknown_id(client):
    todo_id = add(client, title="Same")
    assert "nothing changed" in text_of(call(client, "update_todo", {"id": todo_id, "title": "Same"}))
    missing = call(client, "update_todo", {"id": 999, "status": "done"})
    assert is_error(missing)


@pytest.mark.parametrize("name,arguments", [
    ("add_todo", {}), ("add_todo", {"title": "x", "priority": 9}), ("add_todo", {"title": "x", "priority": True}),
    ("add_todo", {"title": "x", "unknown": 1}), ("update_todo", {"status": "done"}),
    ("update_todo", {"id": 1, "status": "deleted"}), ("update_todo", {"id": "1"}),
    ("list_todos", {"status": "everything"}), ("list_todos", {"limit": 0}),
    ("add_topic_note", {"topic": "t", "note": "n"}), ("mark_recording_analyzed", {}),
    ("get_topic", {}), ("save_digest", {"day": "2026-10-01", "body": 5}),
])
def test_req_006_bad_arguments_are_invalid_params(client, name, arguments):
    assert call(client, name, arguments).json()["error"]["code"] == -32602


@pytest.mark.parametrize("name,arguments", [
    ("add_todo", {"title": "x", "due": "tomorrow"}),
    ("add_topic_note", {"topic": "t", "recording_id": 999, "note": "n"}),
    ("mark_recording_analyzed", {"recording_id": 999}),
    ("save_digest", {"day": "yesterday", "body": "b"}),
    ("get_topic", {"name": "Nothing"}),
])
def test_req_006_domain_errors_are_tool_errors(client, name, arguments):
    assert is_error(call(client, name, arguments))


def test_req_006_topic_notes_timeline_and_overview(client, rec_id):
    add(client, title="Prepare budget", topic="Budget", recording_id=rec_id)
    assert not is_error(call(client, "add_topic_note", {"topic": "Budget", "recording_id": rec_id,
                                                        "note": "First estimate 10k"}))
    call(client, "add_topic_note", {"topic": "Budget", "recording_id": rec_id, "note": "Estimate now 12k"})
    text = text_of(call(client, "get_topic", {"name": "budget"}))
    assert "Prepare budget" in text and "Estimate now 12k" in text and "First estimate" not in text
    assert "Budget | open tasks 1 | notes 1" in text_of(call(client, "list_topics"))


def test_req_006_digest_is_replaced_per_day(client):
    call(client, "save_digest", {"day": "2026-10-05", "body": "first"})
    call(client, "save_digest", {"day": "2026-10-05", "body": "second"})
    call(client, "save_digest", {"day": "2026-10-06", "body": "other"})
    assert text_of(call(client, "list_digests", {"day": "2026-10-05"})).endswith("second")
    latest = text_of(call(client, "list_digests"))
    assert latest.index("2026-10-06") < latest.index("2026-10-05") and "first" not in latest


def test_req_006_analyzed_flag_filters_recordings(client, session_factory):
    with session_factory() as s:
        first, second = add_recording(s, index=1).id, add_recording(s, index=2).id
    assert not is_error(call(client, "mark_recording_analyzed", {"recording_id": first}))
    assert "already marked" in text_of(call(client, "mark_recording_analyzed", {"recording_id": first}))
    only = text_of(call(client, "list_recordings", {"unanalyzed_only": True}))
    assert f"id {second} " in only and f"id {first} " not in only
    assert "verified, analyzed" in text_of(call(client, "list_recordings"))
    with session_factory() as s:
        assert s.get(Recording, first).analyzed_at is not None
