"""Tasks, topics and digests (REQ-006, D-013)."""
import datetime as dt
import inspect
import json

import pytest
from sqlalchemy import select

import todo_service as svc
from database import Digest, Recording, Todo, TodoEvent, Topic
from tests.helpers import add_recording
from todo_service import UNSET, DuplicateTodoError, TodoError

NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone.utc)
LATER = NOW + dt.timedelta(hours=1)


def make(session, title="Send the offer", **kwargs):
    kwargs.setdefault("actor", "claude")
    return svc.create_todo(session, NOW, title=title, **kwargs)


def events(session, todo_id):
    return session.scalars(select(TodoEvent).where(TodoEvent.todo_id == todo_id).order_by(TodoEvent.id)).all()


# --- creating ---------------------------------------------------------------------------------
def test_req_006_create_with_defaults_logs_a_created_event(db_session):
    todo = make(db_session)
    assert (todo.status, todo.priority, todo.created_by, todo.detail, todo.due_date) == ("open", 3, "claude", "", None)
    (event,) = events(db_session, todo.id)
    assert (event.kind, event.actor) == ("created", "claude")
    assert json.loads(event.after_json)["title"] == "Send the offer"


def test_create_with_all_fields_links_recording_and_topic(db_session):
    rec = add_recording(db_session, 1)
    todo = make(db_session, priority=1, detail="Before Friday", due="2026-10-10", topic="Offer Acme",
                recording_id=rec.id, note="said in the call")
    rows, _ = svc.list_todos(db_session, NOW)
    row = rows[0]
    assert (row.priority, row.detail, row.due_date, row.topic, row.recording_id) == (
        1, "Before Friday", dt.date(2026, 10, 10), "Offer Acme", rec.id)
    assert events(db_session, todo.id)[0].note == "said in the call"
    assert events(db_session, todo.id)[0].recording_id == rec.id


def test_topics_are_reused_without_regard_to_case(db_session):
    make(db_session, "First thing to do", topic="Phishing")
    make(db_session, "Second unrelated chore", topic="phishing")
    assert [t.name for t in db_session.scalars(select(Topic))] == ["Phishing"]


@pytest.mark.parametrize("kwargs,message", [
    (dict(title=""), "title must not be empty"), (dict(title="   "), "title must not be empty"),
    (dict(title="x" * 301), "at most 300"), (dict(detail="x" * 4001), "at most 4000"),
    (dict(note="x" * 1001), "at most 1000"), (dict(topic="x" * 121), "at most 120"),
    (dict(priority=0), "priority must be"), (dict(priority=5), "priority must be"),
    (dict(priority="2"), "priority must be"), (dict(priority=True), "priority must be"),
    (dict(priority=2.0), "priority must be"), (dict(due="31.10.2026"), "due must be a date"),
    (dict(due="soon"), "due must be a date"), (dict(recording_id=999), "no recording with id 999"),
    (dict(actor="someone"), "unknown actor"), (dict(title=5), "title must be text"),
])
def test_req_006_invalid_input_is_rejected_and_nothing_is_stored(db_session, kwargs, message):
    args = {"title": "A valid title", "actor": "claude", **kwargs}
    with pytest.raises(TodoError, match=message):
        svc.create_todo(db_session, NOW, **args)
    assert db_session.scalars(select(Todo)).all() == []


def test_a_discarded_recording_cannot_be_linked(db_session):
    rec = add_recording(db_session, 1)
    rec.discarded_at = NOW
    db_session.commit()
    with pytest.raises(TodoError, match="no recording"):
        make(db_session, recording_id=rec.id)


def test_title_is_stripped(db_session):
    assert make(db_session, "  Call the bank  ").title == "Call the bank"


# --- duplicates -------------------------------------------------------------------------------
def test_req_006_a_similar_open_task_is_refused_with_its_id(db_session):
    first = make(db_session, "Send the offer to Acme")
    with pytest.raises(DuplicateTodoError) as exc:
        make(db_session, "Send offer to Acme today")
    assert f"#{first.id}" in str(exc.value) and "allow_similar" in str(exc.value)
    assert db_session.query(Todo).count() == 1


def test_allow_similar_adds_it_anyway(db_session):
    make(db_session, "Send the offer to Acme")
    assert make(db_session, "Send the offer to Acme", allow_similar=True).id == 2


@pytest.mark.parametrize("title", ["Book a flight to Berlin", "Write the quarterly report", "Call"])
def test_different_titles_are_no_duplicates(db_session, title):
    make(db_session, "Send the offer to Acme")
    assert make(db_session, title).id == 2


def test_done_and_dropped_tasks_do_not_block_a_new_one(db_session):
    old = make(db_session, "Send the offer to Acme")
    svc.update_todo(db_session, NOW, old.id, actor="owner", status="done")
    assert make(db_session, "Send the offer to Acme").id == 2


def test_titles_without_long_words_are_compared_as_a_whole(db_session):
    make(db_session, "Do it")
    with pytest.raises(DuplicateTodoError):
        make(db_session, "do it")
    assert make(db_session, "Go on").id == 2


def test_the_most_similar_tasks_are_listed_first(db_session):
    make(db_session, "Prepare board meeting slides draft")                       # id 1, similarity 0.67
    make(db_session, "Prepare the board meeting slides", allow_similar=True)      # id 2, similarity 1.0
    with pytest.raises(DuplicateTodoError) as exc:
        make(db_session, "Prepare the board meeting slides")
    assert [t.id for t in exc.value.similar] == [2, 1]


@pytest.mark.parametrize("title,is_duplicate", [
    ("Prepare board meeting agenda review", False),   # 3 of 6 words shared = 0.50
    ("Prepare board meeting slides agenda", True),    # 4 of 5 words shared = 0.80
])
def test_the_similarity_threshold_separates_duplicates_from_new_tasks(db_session, title, is_duplicate):
    make(db_session, "Prepare board meeting slides")
    if is_duplicate:
        with pytest.raises(DuplicateTodoError):
            make(db_session, title)
    else:
        assert make(db_session, title).id == 2


# --- updating ---------------------------------------------------------------------------------
@pytest.mark.parametrize("field,value,expected", [
    ("title", "A new title", "A new title"), ("detail", "More words", "More words"), ("priority", 1, 1),
])
def test_req_006_a_change_is_logged_with_old_and_new_value(db_session, field, value, expected):
    todo = make(db_session)
    before = getattr(todo, field)
    _, changed = svc.update_todo(db_session, LATER, todo.id, actor="owner", **{field: value})
    assert changed is True
    event = events(db_session, todo.id)[-1]
    assert (event.kind, event.actor) == ("updated", "owner")
    assert json.loads(event.before_json) == {field: before} and json.loads(event.after_json) == {field: expected}


def test_due_and_topic_can_be_set_and_cleared(db_session):
    todo = make(db_session, due="2026-10-10", topic="Acme")
    svc.update_todo(db_session, LATER, todo.id, actor="claude", due="", topic="")
    db_session.refresh(todo)
    assert todo.due_date is None and todo.topic_id is None
    svc.update_todo(db_session, LATER, todo.id, actor="claude", due="2026-11-01", topic="Other")
    row = svc.list_todos(db_session, LATER)[0][0]
    assert (row.due_date, row.topic) == (dt.date(2026, 11, 1), "Other")


def test_req_006_complete_sets_done_at_and_reopen_clears_it(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", status="done", recording_id=None, note="said it is done")
    db_session.refresh(todo)
    assert todo.status == "done" and todo.done_at is not None
    assert [e.kind for e in events(db_session, todo.id)] == ["created", "completed"]
    svc.update_todo(db_session, LATER, todo.id, actor="owner", status="open")
    db_session.refresh(todo)
    assert todo.status == "open" and todo.done_at is None
    assert events(db_session, todo.id)[-1].kind == "reopened"


def test_dropping_is_a_status_not_a_deletion(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", status="dropped")
    assert events(db_session, todo.id)[-1].kind == "dropped"
    assert db_session.get(Todo, todo.id).status == "dropped"


def test_status_and_other_fields_in_one_call_are_one_event(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", status="done", detail="finished by phone")
    event = events(db_session, todo.id)[-1]
    assert event.kind == "completed" and "detail" in json.loads(event.after_json)
    assert len(events(db_session, todo.id)) == 2


def test_an_update_without_a_difference_changes_nothing_and_logs_nothing(db_session):
    todo = make(db_session, priority=2, topic="Acme", due="2026-10-10")
    for kwargs in ({}, {"priority": 2}, {"title": todo.title}, {"topic": "ACME"}, {"due": "2026-10-10"}, {"status": "open"}):
        _, changed = svc.update_todo(db_session, LATER, todo.id, actor="claude", **kwargs)
        assert changed is False
    assert len(events(db_session, todo.id)) == 1


def test_update_records_the_source_recording_and_note(db_session):
    rec = add_recording(db_session, 1)
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", priority=1, recording_id=rec.id, note="now urgent")
    event = events(db_session, todo.id)[-1]
    assert (event.recording_id, event.note) == (rec.id, "now urgent")


@pytest.mark.parametrize("kwargs,message", [
    ({"title": ""}, "must not be empty"), ({"priority": 9}, "priority must be"), ({"status": "finished"}, "status must be"),
    ({"due": "tomorrow"}, "due must be a date"), ({"detail": "x" * 4001}, "at most 4000"),
    ({"topic": "x" * 121}, "at most 120"), ({"note": "x" * 1001}, "at most 1000"), ({"recording_id": 999}, "no recording"),
])
def test_invalid_updates_are_rejected_and_leave_the_task_alone(db_session, kwargs, message):
    todo = make(db_session)
    with pytest.raises(TodoError, match=message):
        svc.update_todo(db_session, LATER, todo.id, actor="claude", **kwargs)
    assert len(events(db_session, todo.id)) == 1 and db_session.get(Todo, todo.id).priority == 3


def test_unknown_task_is_an_error(db_session):
    with pytest.raises(TodoError, match="no task with id 42"):
        svc.update_todo(db_session, LATER, 42, actor="claude", priority=1)


# --- undo -------------------------------------------------------------------------------------
def test_req_006_undoing_a_completion_reopens_the_task(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", status="done")
    completed = events(db_session, todo.id)[-1]
    svc.undo_event(db_session, LATER, completed.id)
    db_session.refresh(todo)
    assert todo.status == "open" and todo.done_at is None
    last = events(db_session, todo.id)[-1]
    assert (last.kind, last.actor) == ("undone", "owner")


def test_undo_restores_priority_due_and_topic(db_session):
    todo = make(db_session, priority=3, due="2026-10-10", topic="Acme")
    svc.update_todo(db_session, LATER, todo.id, actor="claude", priority=1, due="2026-12-01", topic="Other")
    svc.undo_event(db_session, LATER, events(db_session, todo.id)[-1].id)
    row = svc.list_todos(db_session, LATER)[0][0]
    assert (row.priority, row.due_date, row.topic) == (3, dt.date(2026, 10, 10), "Acme")


def test_undoing_a_reopen_restores_the_done_time(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="owner", status="done")
    done_at = db_session.get(Todo, todo.id).done_at
    svc.update_todo(db_session, LATER, todo.id, actor="owner", status="open")
    svc.undo_event(db_session, LATER, events(db_session, todo.id)[-1].id)
    db_session.refresh(todo)
    assert todo.status == "done" and todo.done_at.replace(tzinfo=None) == done_at.replace(tzinfo=None)


def test_only_the_latest_change_can_be_undone(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", priority=1)
    first_change = events(db_session, todo.id)[-1]
    svc.update_todo(db_session, LATER, todo.id, actor="claude", detail="more")
    with pytest.raises(TodoError, match="only the latest"):
        svc.undo_event(db_session, LATER, first_change.id)


@pytest.mark.parametrize("which", ["created", "undone"])
def test_creation_and_undo_entries_cannot_be_undone(db_session, which):
    todo = make(db_session)
    if which == "undone":
        svc.update_todo(db_session, LATER, todo.id, actor="claude", priority=1)
        svc.undo_event(db_session, LATER, events(db_session, todo.id)[-1].id)
    with pytest.raises(TodoError, match="cannot be undone"):
        svc.undo_event(db_session, LATER, events(db_session, todo.id)[-1 if which == "undone" else 0].id)


def test_the_same_change_cannot_be_undone_twice(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", priority=1)
    change = events(db_session, todo.id)[-1]
    svc.undo_event(db_session, LATER, change.id)
    with pytest.raises(TodoError):
        svc.undo_event(db_session, LATER, change.id)


def test_undo_is_refused_when_the_task_was_edited_outside_the_log(db_session):
    todo = make(db_session)
    svc.update_todo(db_session, LATER, todo.id, actor="claude", priority=1)
    change = events(db_session, todo.id)[-1]
    todo.priority = 4
    db_session.commit()
    with pytest.raises(TodoError, match="changed since"):
        svc.undo_event(db_session, LATER, change.id)


def test_unknown_event_cannot_be_undone(db_session):
    with pytest.raises(TodoError, match="no change with id"):
        svc.undo_event(db_session, LATER, 999)


# --- listing ----------------------------------------------------------------------------------
def test_req_006_tasks_are_sorted_by_priority_then_due_date_then_age(db_session):
    make(db_session, "Normal without date")
    svc.create_todo(db_session, NOW + dt.timedelta(minutes=1), title="Normal due late", actor="claude", due="2026-11-30")
    svc.create_todo(db_session, NOW + dt.timedelta(minutes=2), title="Normal due soon", actor="claude", due="2026-10-09")
    svc.create_todo(db_session, NOW + dt.timedelta(minutes=3), title="Low priority chore", actor="claude", priority=4)
    svc.create_todo(db_session, NOW + dt.timedelta(minutes=4), title="Urgent matter now", actor="claude", priority=1)
    svc.create_todo(db_session, NOW + dt.timedelta(minutes=5), title="High priority item", actor="claude", priority=2)
    titles = [r.title for r in svc.list_todos(db_session, LATER)[0]]
    assert titles == ["Urgent matter now", "High priority item", "Normal due soon", "Normal due late",
                      "Normal without date", "Low priority chore"]


def test_list_filters_by_status_and_topic(db_session):
    a = make(db_session, "Alpha task here", topic="Acme")
    make(db_session, "Beta task there", topic="Other")
    svc.update_todo(db_session, LATER, a.id, actor="owner", status="done")
    assert [r.title for r in svc.list_todos(db_session, LATER)[0]] == ["Beta task there"]
    assert [r.title for r in svc.list_todos(db_session, LATER, status="done")[0]] == ["Alpha task here"]
    assert len(svc.list_todos(db_session, LATER, status="all")[0]) == 2
    assert [r.title for r in svc.list_todos(db_session, LATER, status="all", topic="acme")[0]] == ["Alpha task here"]
    assert svc.list_todos(db_session, LATER, topic="nonexistent") == ([], False)
    with pytest.raises(TodoError):
        svc.list_todos(db_session, LATER, status="finished")


def test_list_limit_reports_truncation(db_session):
    for i in range(5):
        make(db_session, f"Distinct chore number{i}x", allow_similar=True)
    rows, truncated = svc.list_todos(db_session, LATER, limit=3)
    assert len(rows) == 3 and truncated is True
    assert svc.list_todos(db_session, LATER, limit=5)[1] is False


def test_age_is_counted_in_whole_days(db_session):
    make(db_session)
    assert svc.list_todos(db_session, NOW + dt.timedelta(days=3, hours=5))[0][0].age_days == 3


def test_counts_and_get_todo(db_session):
    a = make(db_session, "Alpha task here")
    make(db_session, "Beta task there")
    svc.update_todo(db_session, LATER, a.id, actor="owner", status="done")
    assert svc.counts(db_session) == {"open": 1, "done": 1, "dropped": 0}
    row, evs = svc.get_todo(db_session, LATER, a.id)
    assert row.status == "done" and [e.kind for e in evs] == ["completed", "created"]
    assert svc.get_todo(db_session, LATER, 999) is None


# --- topics -----------------------------------------------------------------------------------
def test_req_006_topic_notes_are_one_per_recording_and_updated_in_place(db_session):
    rec = add_recording(db_session, 1)
    svc.add_topic_note(db_session, NOW, topic="Phishing", recording_id=rec.id, note="first view")
    svc.add_topic_note(db_session, LATER, topic="phishing", recording_id=rec.id, note="second view")
    topic, entries = svc.topic_timeline(db_session, "PHISHING")
    assert [e["text"] for e in entries if e["kind"] == "note"] == ["second view"]


@pytest.mark.parametrize("kwargs,message", [
    (dict(note=""), "must not be empty"), (dict(note="x" * 1001), "at most 1000"),
    (dict(topic=""), "must not be empty"), (dict(recording_id=999), "no recording"),
])
def test_invalid_topic_notes_are_rejected(db_session, kwargs, message):
    rec = add_recording(db_session, 1)
    args = {"topic": "Acme", "recording_id": rec.id, "note": "something", **kwargs}
    with pytest.raises(TodoError, match=message):
        svc.add_topic_note(db_session, NOW, **args)


def test_req_006_timeline_mixes_notes_and_task_events_newest_first(db_session):
    early = add_recording(db_session, 1, started=dt.datetime(2026, 10, 1, 9, tzinfo=dt.timezone.utc))
    late = add_recording(db_session, 2, started=dt.datetime(2026, 10, 5, 9, tzinfo=dt.timezone.utc))
    svc.add_topic_note(db_session, NOW, topic="Acme", recording_id=early.id, note="offer discussed")
    svc.add_topic_note(db_session, NOW, topic="Acme", recording_id=late.id, note="offer sent")
    todo = svc.create_todo(db_session, dt.datetime(2026, 10, 3, 9, tzinfo=dt.timezone.utc), title="Send the offer",
                           actor="claude", topic="Acme", recording_id=early.id)
    svc.update_todo(db_session, dt.datetime(2026, 10, 6, 9, tzinfo=dt.timezone.utc), todo.id, actor="owner", status="done")
    topic, entries = svc.topic_timeline(db_session, "acme")
    assert [(e["kind"], e["text"]) for e in entries] == [
        ("completed", "Send the offer"), ("note", "offer sent"), ("created", "Send the offer"), ("note", "offer discussed")]
    assert svc.topic_timeline(db_session, "unknown") is None


def test_topics_are_listed_with_open_counts_busiest_first(db_session):
    rec = add_recording(db_session, 1)
    make(db_session, "Alpha task here", topic="Quiet")
    make(db_session, "Beta task there", topic="Busy")
    make(db_session, "Gamma chore somewhere", topic="Busy")
    svc.add_topic_note(db_session, NOW, topic="Notes only", recording_id=rec.id, note="n")
    listing = [(t.name, open_n, notes) for t, open_n, notes, _ in svc.list_topics(db_session)]
    assert listing == [("Busy", 2, 0), ("Quiet", 1, 0), ("Notes only", 0, 1)]


# --- digests ----------------------------------------------------------------------------------
def test_req_006_a_digest_is_rewritten_when_saved_again_for_the_same_day(db_session):
    svc.save_digest(db_session, NOW, "2026-10-07", "first version")
    svc.save_digest(db_session, LATER, "2026-10-07", "second version")
    assert db_session.query(Digest).count() == 1
    assert svc.get_digest(db_session, "2026-10-07").body == "second version"


def test_digests_are_listed_newest_day_first_with_a_limit(db_session):
    for day in ("2026-10-05", "2026-10-07", "2026-10-06"):
        svc.save_digest(db_session, NOW, day, f"body {day}")
    assert [d.day.isoformat() for d in svc.list_digests(db_session)] == ["2026-10-07", "2026-10-06", "2026-10-05"]
    assert len(svc.list_digests(db_session, limit=2)) == 2
    assert svc.get_digest(db_session, "2026-01-01") is None


@pytest.mark.parametrize("day,body,message", [
    ("", "text", "day must be a date"), ("7.10.2026", "text", "day must be a date"),
    ("2026-10-07", "", "must not be empty"), ("2026-10-07", "x" * 20001, "at most 20000"),
])
def test_invalid_digests_are_rejected(db_session, day, body, message):
    with pytest.raises(TodoError, match=message):
        svc.save_digest(db_session, NOW, day, body)


# --- analysis marker --------------------------------------------------------------------------
def test_req_006_a_recording_is_marked_analyzed_once(db_session):
    rec = add_recording(db_session, 1)
    assert svc.mark_analyzed(db_session, NOW, rec.id) is True
    assert svc.mark_analyzed(db_session, LATER, rec.id) is False
    db_session.refresh(rec)
    assert rec.analyzed_at.replace(tzinfo=None) == NOW.replace(tzinfo=None)


def test_unknown_or_discarded_recordings_cannot_be_marked(db_session):
    rec = add_recording(db_session, 1)
    rec.discarded_at = NOW
    db_session.commit()
    for rec_id in (rec.id, 999):
        with pytest.raises(TodoError, match="no recording"):
            svc.mark_analyzed(db_session, NOW, rec_id)


# --- what this module can never do ------------------------------------------------------------------
def test_req_006_the_service_has_no_way_to_delete_anything():
    source = inspect.getsource(svc)
    assert "session.delete" not in source and "delete(" not in source and ".query(" not in source
    assert not [name for name in dir(svc) if name.startswith(("delete", "remove", "purge"))]


def test_recording_content_is_never_modified_by_the_routine_functions(db_session):
    rec = add_recording(db_session, 1, segments=3)
    before = (rec.title, rec.summary, rec.segment_count, rec.content_hash)
    svc.mark_analyzed(db_session, NOW, rec.id)
    svc.add_topic_note(db_session, NOW, topic="Acme", recording_id=rec.id, note="n")
    make(db_session, recording_id=rec.id)
    db_session.refresh(rec)
    assert (rec.title, rec.summary, rec.segment_count, rec.content_hash) == before
