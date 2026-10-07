"""
Tasks, topics and daily overviews taken from the conversations (REQ-006, D-013).

All rules live here; the MCP tools and the web UI only call these functions. Nothing
in this module can delete a task, a topic, a note, a digest or a recording: the most
severe change is marking a task `dropped`, and every change is logged with its old and
new values so it can be undone.
"""
import datetime as dt
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import Digest, Recording, TextVersion, Todo, TodoEvent, Topic, TopicNote
from utils import as_utc, day_bounds, local_day

log = logging.getLogger(__name__)

PRIORITIES = (1, 2, 3, 4)  # 1 urgent, 2 high, 3 normal, 4 low
DEFAULT_PRIORITY = 3
STATUSES = ("open", "done", "dropped")
ACTORS = ("claude", "owner")
TITLE_MAX, DETAIL_MAX, NOTE_MAX, TOPIC_MAX, DIGEST_MAX = 300, 4000, 1000, 120, 20000
LIST_LIMIT_MAX = 200
MAX_OPEN_TASKS = 2000
SIMILARITY_THRESHOLD = 0.6
UNDOABLE_KINDS = ("updated", "completed", "reopened", "dropped")
UNSET = object()  # "argument not given", as opposed to None or "" which clear a field


class TodoError(ValueError):
    """A rejected request. The message is safe to show to the caller."""


class DuplicateTodoError(TodoError):
    def __init__(self, similar: list):
        self.similar = similar
        listed = "; ".join(f"#{t.id} {t.title}" for t in similar)
        super().__init__(f"a similar open task exists: {listed}. Update it, or pass allow_similar to add it anyway")


@dataclass
class TodoRow:
    id: int
    title: str
    detail: str
    priority: int
    status: str
    due_date: Optional[dt.date]
    topic: Optional[str]
    recording_id: Optional[int]
    created_by: str
    created_at: dt.datetime
    done_at: Optional[dt.datetime]
    age_days: int
    topic_id: Optional[int] = None


# --- validation -------------------------------------------------------------------------
def _text(value, name: str, max_chars: int, required: bool = False) -> str:
    if not isinstance(value, str):
        raise TodoError(f"{name} must be text")
    value = value.strip()
    if required and not value:
        raise TodoError(f"{name} must not be empty")
    if len(value) > max_chars:
        raise TodoError(f"{name} must have at most {max_chars} characters")
    return value


def _priority(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in PRIORITIES:
        raise TodoError("priority must be 1 (urgent), 2 (high), 3 (normal) or 4 (low)")
    return value


def _date(value, name: str) -> Optional[dt.date]:
    """An ISO date, or None for empty. Raises TodoError naming the field."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TodoError(f"{name} must be a date like 2026-10-31") from exc


def _due(value) -> Optional[dt.date]:
    return _date(value, "due")


def _status(value) -> str:
    if value not in STATUSES:
        raise TodoError("status must be open, done or dropped")
    return value


def _actor(value: str) -> str:
    if value not in ACTORS:
        raise TodoError("unknown actor")
    return value


def _visible_recording(session: Session, recording_id: Optional[int]) -> Optional[int]:
    if recording_id is None:
        return None
    rec = session.get(Recording, recording_id)
    if rec is None or rec.discarded_at is not None:
        raise TodoError(f"no recording with id {recording_id}")
    return recording_id


# --- topics -----------------------------------------------------------------------------
def get_or_create_topic(session: Session, name: str) -> Topic:
    name = _text(name, "topic", TOPIC_MAX, required=True)
    topic = session.scalar(select(Topic).where(func.lower(Topic.name) == name.lower()))
    if topic is None:
        topic = Topic(name=name)
        session.add(topic)
        session.flush()
    return topic


def _writable_topic(session: Session, name: str) -> Topic:
    """The topic for a new write. Topics the owner excluded take nothing new."""
    topic = get_or_create_topic(session, name)
    if topic.excluded:
        raise TodoError(f"the topic '{topic.name}' is excluded by the owner: record nothing about it")
    return topic


def set_topic_excluded(session: Session, name: str, excluded: bool) -> Topic:
    """Owner only (no MCP tool). Excluding keeps what exists but stops new tasks and notes."""
    topic = get_or_create_topic(session, name)
    topic.excluded = bool(excluded)
    session.commit()
    log.info("Topic %s id=%s", "excluded" if topic.excluded else "allowed again", topic.id)
    return topic


def rename_topic(session: Session, topic_id: int, new_name: str) -> Topic:
    """Owner only (no MCP tool). Tasks and notes follow because they point at the id.

    A name that another topic already has (case-insensitive) is refused; merging is not offered.
    """
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise TodoError(f"no topic with id {topic_id}")
    new_name = _text(new_name, "topic", TOPIC_MAX, required=True)
    clash = session.scalar(select(Topic).where(func.lower(Topic.name) == new_name.lower(), Topic.id != topic_id))
    if clash is not None:
        raise TodoError(f"a topic named '{clash.name}' exists already")
    if new_name != topic.name:
        log.info("Topic renamed id=%s", topic.id)
        topic.name = new_name
        session.commit()
    return topic


def _excluded_ids(session: Session) -> list:
    return list(session.scalars(select(Topic.id).where(Topic.excluded.is_(True))))


def detach_topic_tasks(session: Session, now: dt.datetime, topic: Topic, actor: str, note: str) -> int:
    """Take the tasks out of a topic (each change is logged and can be undone). Returns how many."""
    tasks = session.scalars(select(Todo).where(Todo.topic_id == topic.id)).all()
    for todo in tasks:
        todo.topic_id = None
        todo.updated_at = now
        _log(session, now, todo, "updated", _actor(actor), None, note, {"topic": topic.name}, {"topic": None})
    session.flush()
    return len(tasks)


def _topic_name(session: Session, topic_id: Optional[int]) -> Optional[str]:
    return session.get(Topic, topic_id).name if topic_id else None


# --- tasks ------------------------------------------------------------------------------
def _tokens(title: str) -> set:
    return set(re.findall(r"\w{3,}", title.lower()))


def similar_open(session: Session, title: str, limit: int = 3) -> list:
    """Open tasks whose title is close to `title` (word overlap), most similar first."""
    wanted = _tokens(title)
    scored = []
    stmt = select(Todo).where(Todo.status == "open")
    hidden = _excluded_ids(session)
    if hidden:  # the tasks of an excluded topic must not show up, not even as "a similar task exists"
        stmt = stmt.where(Todo.topic_id.is_(None) | Todo.topic_id.not_in(hidden))
    for todo in session.scalars(stmt):
        other = _tokens(todo.title)
        if not wanted or not other:
            score = 1.0 if todo.title.strip().lower() == title.strip().lower() else 0.0
        else:
            score = len(wanted & other) / len(wanted | other)
        if score >= SIMILARITY_THRESHOLD:
            scored.append((score, todo))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [todo for _, todo in scored[:limit]]


FIELDS = ("title", "detail", "priority", "status", "due", "topic", "done_at")


def _snapshot(session: Session, todo: Todo, fields=FIELDS) -> dict:
    """The named fields of a task as plain, comparable values (dates as ISO text)."""
    values = {
        "title": todo.title, "detail": todo.detail, "priority": todo.priority, "status": todo.status,
        "due": todo.due_date.isoformat() if todo.due_date else None,
        "topic": _topic_name(session, todo.topic_id),
        "done_at": as_utc(todo.done_at).isoformat() if todo.done_at else None,
    }
    return {k: values[k] for k in fields}


def _apply(session: Session, todo: Todo, values: dict) -> None:
    """Write snapshot-style values back to a task. Used for changes and for undoing them."""
    if "title" in values:
        todo.title = values["title"]
    if "detail" in values:
        todo.detail = values["detail"]
    if "priority" in values:
        todo.priority = values["priority"]
    if "due" in values:
        todo.due_date = _due(values["due"])
    if "topic" in values:
        todo.topic_id = get_or_create_topic(session, values["topic"]).id if values["topic"] else None
    if "status" in values:
        todo.status = values["status"]
    if "done_at" in values:
        todo.done_at = dt.datetime.fromisoformat(values["done_at"]) if values["done_at"] else None


def _log(session: Session, now: dt.datetime, todo: Todo, kind: str, actor: str, recording_id, note: str,
         before: dict, after: dict) -> TodoEvent:
    event = TodoEvent(todo_id=todo.id, kind=kind, actor=actor, recording_id=recording_id, note=note,
                      before_json=json.dumps(before, sort_keys=True, ensure_ascii=False),
                      after_json=json.dumps(after, sort_keys=True, ensure_ascii=False), created_at=now)
    session.add(event)
    return event


def create_todo(session: Session, now: dt.datetime, *, title: str, actor: str, priority=DEFAULT_PRIORITY,
                detail: str = "", due=None, topic: Optional[str] = None, recording_id: Optional[int] = None,
                note: str = "", allow_similar: bool = False) -> Todo:
    actor = _actor(actor)
    title = _text(title, "title", TITLE_MAX, required=True)
    detail = _text(detail, "detail", DETAIL_MAX)
    note = _text(note, "note", NOTE_MAX)
    priority = _priority(priority)
    due_date = _due(due)
    recording_id = _visible_recording(session, recording_id)
    open_tasks = session.scalar(select(func.count()).select_from(Todo).where(Todo.status == "open"))
    if open_tasks >= MAX_OPEN_TASKS:
        raise TodoError(f"there are {open_tasks} open tasks already; finish or drop some first")
    if not allow_similar:
        similar = similar_open(session, title)
        if similar:
            raise DuplicateTodoError(similar)
    topic_row = _writable_topic(session, topic) if topic else None
    todo = Todo(title=title, detail=detail, priority=priority, status="open", due_date=due_date,
                topic_id=topic_row.id if topic_row else None, recording_id=recording_id, created_by=actor,
                created_at=now, updated_at=now)
    session.add(todo)
    session.flush()
    _log(session, now, todo, "created", actor, recording_id, note, {},
         _snapshot(session, todo, ("title", "priority", "status", "due", "topic")))
    session.commit()
    log.info("Todo created id=%s actor=%s priority=%s", todo.id, actor, priority)
    return todo


def _requested_changes(todo_fields: dict) -> dict:
    """Validate the given fields and return them as snapshot-style values."""
    wanted = {}
    if todo_fields["title"] is not UNSET:
        wanted["title"] = _text(todo_fields["title"], "title", TITLE_MAX, required=True)
    if todo_fields["detail"] is not UNSET:
        wanted["detail"] = _text(todo_fields["detail"], "detail", DETAIL_MAX)
    if todo_fields["priority"] is not UNSET:
        wanted["priority"] = _priority(todo_fields["priority"])
    if todo_fields["due"] is not UNSET:
        due = _due(todo_fields["due"])
        wanted["due"] = due.isoformat() if due else None
    if todo_fields["topic"] is not UNSET:
        wanted["topic"] = _text(todo_fields["topic"], "topic", TOPIC_MAX) or None
    if todo_fields["status"] is not UNSET:
        wanted["status"] = _status(todo_fields["status"])
    return wanted


def _differs(field: str, wanted, current) -> bool:
    if field == "topic":  # topic names are matched without regard to case
        return (wanted or "").lower() != (current or "").lower()
    return wanted != current


def update_todo(session: Session, now: dt.datetime, todo_id: int, *, actor: str, title=UNSET, detail=UNSET,
                priority=UNSET, due=UNSET, topic=UNSET, status=UNSET, recording_id: Optional[int] = None,
                note: str = "") -> tuple:
    """Change a task. Returns (todo, changed). Fields left at UNSET stay as they are."""
    actor = _actor(actor)
    todo = session.get(Todo, todo_id)
    if todo is None or (actor == "claude" and todo.topic_id in _excluded_ids(session)):
        # Same answer for both: Claude learns nothing about tasks in a topic the owner excluded.
        raise TodoError(f"no task with id {todo_id}")
    note = _text(note, "note", NOTE_MAX)
    recording_id = _visible_recording(session, recording_id)
    wanted = _requested_changes({"title": title, "detail": detail, "priority": priority, "due": due,
                                 "topic": topic, "status": status})
    current = _snapshot(session, todo)
    changes = {k: v for k, v in wanted.items() if _differs(k, v, current[k])}
    if not changes:
        return todo, False
    if changes.get("topic"):
        _writable_topic(session, changes["topic"])

    kind = "updated"
    if "status" in changes:
        changes["done_at"] = now.isoformat() if changes["status"] == "done" else None
        kind = {"done": "completed", "open": "reopened", "dropped": "dropped"}[changes["status"]]
    before = {k: current[k] for k in changes}
    _apply(session, todo, changes)
    todo.updated_at = now
    session.flush()
    _log(session, now, todo, kind, actor, recording_id, note, before, _snapshot(session, todo, tuple(changes)))
    session.commit()
    log.info("Todo %s id=%s actor=%s fields=%s", kind, todo.id, actor, sorted(changes))
    return todo, True


def undo_event(session: Session, now: dt.datetime, event_id: int, actor: str = "owner") -> Todo:
    """Take back the latest change of a task, if nothing happened to it since."""
    actor = _actor(actor)
    event = session.get(TodoEvent, event_id)
    if event is None:
        raise TodoError(f"no change with id {event_id}")
    if event.kind not in UNDOABLE_KINDS:
        raise TodoError("this entry cannot be undone")
    latest = session.scalar(select(func.max(TodoEvent.id)).where(TodoEvent.todo_id == event.todo_id))
    if latest != event.id:
        raise TodoError("only the latest change of a task can be undone")
    todo = session.get(Todo, event.todo_id)
    before, after = json.loads(event.before_json), json.loads(event.after_json)
    if _snapshot(session, todo, tuple(after)) != after:
        raise TodoError("the task was changed since, nothing to undo")
    if before.get("topic"):
        restored = session.scalar(select(Topic).where(func.lower(Topic.name) == before["topic"].lower()))
        if restored is not None and restored.excluded:
            raise TodoError(f"the topic '{restored.name}' is excluded, the task cannot go back into it")
    _apply(session, todo, before)
    todo.updated_at = now
    session.flush()
    _log(session, now, todo, "undone", actor, None, f"undid change {event.id}", after, before)
    session.commit()
    log.info("Todo change undone id=%s event=%s actor=%s", todo.id, event.id, actor)
    return todo


def _rows(session: Session, todos: list, now: dt.datetime) -> list:
    topics = {t.id: t.name for t in session.scalars(select(Topic))}
    return [
        TodoRow(t.id, t.title, t.detail, t.priority, t.status, t.due_date, topics.get(t.topic_id), t.recording_id,
                t.created_by, as_utc(t.created_at), as_utc(t.done_at) if t.done_at else None,
                max((now - as_utc(t.created_at)).days, 0), t.topic_id)
        for t in todos
    ]


SORTS = ("priority", "due", "newest", "oldest", "topic", "title")


def _order(sort: str) -> tuple:
    """ORDER BY terms for a sort name. The id always breaks ties so paging and tests are stable."""
    if sort not in SORTS:
        raise TodoError(f"sort must be one of: {', '.join(SORTS)}")
    by_priority = (Todo.priority, Todo.due_date.is_(None), Todo.due_date, Todo.created_at, Todo.id)
    return {
        "priority": by_priority,
        "due": (Todo.due_date.is_(None), Todo.due_date, Todo.priority, Todo.created_at, Todo.id),
        "newest": (Todo.created_at.desc(), Todo.id.desc()),
        "oldest": (Todo.created_at, Todo.id),
        "topic": (Topic.name.is_(None), func.lower(Topic.name), *by_priority),
        "title": (func.lower(Todo.title), Todo.id),
    }[sort]


def list_todos(session: Session, now: dt.datetime, *, status: str = "open", topic: Optional[str] = None,
               limit: int = 50, include_excluded: bool = False, sort: str = "priority") -> tuple:
    """Without tasks of excluded topics; most urgent first unless `sort` says otherwise.
    Returns (rows, truncated)."""
    limit = min(max(limit, 1), LIST_LIMIT_MAX)
    stmt = select(Todo).outerjoin(Topic, Topic.id == Todo.topic_id)
    if not include_excluded:
        hidden = _excluded_ids(session)
        if hidden:
            stmt = stmt.where(Todo.topic_id.is_(None) | Todo.topic_id.not_in(hidden))
    if status != "all":
        stmt = stmt.where(Todo.status == _status(status))
    if topic:
        found = session.scalar(select(Topic).where(func.lower(Topic.name) == topic.strip().lower()))
        if found is None:
            return [], False
        stmt = stmt.where(Todo.topic_id == found.id)
    stmt = stmt.order_by(*_order(sort)).limit(limit + 1)
    found_rows = session.scalars(stmt).all()
    return _rows(session, found_rows[:limit], now), len(found_rows) > limit


def get_todo(session: Session, now: dt.datetime, todo_id: int):
    """Return (row, events newest first) or None."""
    todo = session.get(Todo, todo_id)
    if todo is None:
        return None
    events = session.scalars(select(TodoEvent).where(TodoEvent.todo_id == todo_id)
                             .order_by(TodoEvent.id.desc())).all()
    return _rows(session, [todo], now)[0], events


def counts(session: Session) -> dict:
    result = {s: 0 for s in STATUSES}
    stmt = select(Todo.status, func.count()).group_by(Todo.status)
    hidden = _excluded_ids(session)
    if hidden:
        stmt = stmt.where(Todo.topic_id.is_(None) | Todo.topic_id.not_in(hidden))
    for status, number in session.execute(stmt):
        result[status] = number
    return result


# --- topics and their history --------------------------------------------------------------
def add_topic_note(session: Session, now: dt.datetime, *, topic: str, recording_id: int, note: str) -> TopicNote:
    note = _text(note, "note", NOTE_MAX, required=True)
    recording_id = _visible_recording(session, recording_id)
    if recording_id is None:
        raise TodoError("recording_id is required")
    topic_row = _writable_topic(session, topic)
    existing = session.scalar(select(TopicNote).where(TopicNote.topic_id == topic_row.id,
                                                      TopicNote.recording_id == recording_id))
    if existing is None:
        existing = TopicNote(topic_id=topic_row.id, recording_id=recording_id, note=note, created_at=now, updated_at=now)
        session.add(existing)
    else:
        if existing.note != note:
            _remember(session, "topic_note", str(existing.id), existing.note, now)
        existing.note, existing.updated_at = note, now
    session.commit()
    return existing


def list_topics(session: Session) -> list:
    """(topic, open task count, note count, last activity) for every topic, busiest first."""
    result = []
    for topic in session.scalars(select(Topic).order_by(Topic.name)):
        open_count = session.scalar(select(func.count()).select_from(Todo)
                                    .where(Todo.topic_id == topic.id, Todo.status == "open"))
        notes = session.scalars(select(TopicNote).where(TopicNote.topic_id == topic.id)).all()
        todos = session.scalars(select(Todo).where(Todo.topic_id == topic.id)).all()
        stamps = [as_utc(n.updated_at) for n in notes] + [as_utc(t.updated_at) for t in todos]
        result.append((topic, open_count, len(notes), max(stamps) if stamps else as_utc(topic.created_at)))
    result.sort(key=lambda item: (-item[1], item[0].name.lower()))
    return result


def topic_timeline(session: Session, name: str):
    """Everything about one topic, found by name (MCP): (topic, entries) or None."""
    topic = session.scalar(select(Topic).where(func.lower(Topic.name) == name.strip().lower()))
    return None if topic is None else (topic, _timeline(session, topic))


def topic_timeline_by_id(session: Session, topic_id: int):
    """The same, found by id (web UI; ids are safe in addresses, names are not): (topic, entries) or None."""
    topic = session.get(Topic, topic_id)
    return None if topic is None else (topic, _timeline(session, topic))


def _timeline(session: Session, topic: Topic) -> list:
    """Notes and task events of a topic, newest first. Each entry is a dict."""
    entries = []
    for note in session.scalars(select(TopicNote).where(TopicNote.topic_id == topic.id)):
        rec = session.get(Recording, note.recording_id)
        entries.append({"kind": "note", "at": as_utc(rec.started_at), "text": note.note,
                        "recording_id": note.recording_id, "todo_id": None, "actor": "claude"})
    for todo in session.scalars(select(Todo).where(Todo.topic_id == topic.id)):
        for event in session.scalars(select(TodoEvent).where(TodoEvent.todo_id == todo.id)):
            text = f"{todo.title}" + (f" ({event.note})" if event.note else "")
            entries.append({"kind": event.kind, "at": as_utc(event.created_at), "text": text,
                            "recording_id": event.recording_id, "todo_id": todo.id, "actor": event.actor})
    entries.sort(key=lambda e: e["at"], reverse=True)
    return entries


# --- daily overviews ---------------------------------------------------------------------------
def recordings_of_day(session: Session, day: dt.date) -> list:
    """The stored recordings that started on this local day (04:00 boundary), oldest first."""
    start, end = day_bounds(day)
    return session.scalars(select(Recording).where(
        Recording.discarded_at.is_(None), Recording.started_at >= start, Recording.started_at < end,
    ).order_by(Recording.started_at)).all()


def save_digest(session: Session, now: dt.datetime, day, body: str) -> Digest:
    """Save the overview of the day the conversations took place (Plaud's date), not the run date."""
    day = _date(day, "day")
    if day is None:
        raise TodoError("day must be a date like 2026-10-31")
    body = _text(body, "body", DIGEST_MAX, required=True)
    if not recordings_of_day(session, day):
        recent = session.scalars(select(Recording.started_at).where(Recording.discarded_at.is_(None))
                                 .order_by(Recording.started_at.desc()).limit(30)).all()
        days = sorted({local_day(s) for s in recent}, reverse=True)[:7]
        known = ", ".join(d.isoformat() for d in days) or "none"
        raise TodoError(f"no recording started on {day.isoformat()}. The day is the day of the conversations "
                        f"(local time, a day runs from 04:00 to 04:00). Days with recordings: {known}")
    digest = session.scalar(select(Digest).where(Digest.day == day))
    if digest is None:
        digest = Digest(day=day, body=body, created_at=now, updated_at=now)
        session.add(digest)
    else:
        if digest.body != body:
            _remember(session, "digest", day.isoformat(), digest.body, now)
        digest.body, digest.updated_at = body, now
    session.commit()
    return digest


def _remember(session: Session, kind: str, ref: str, body: str, now: dt.datetime) -> None:
    """Keep the text that a write is about to replace (D-020): an overwrite can be read and restored."""
    session.add(TextVersion(kind=kind, ref=ref, body=body, saved_at=now))


def digest_versions(session: Session, day: dt.date) -> list:
    """Earlier texts of the overview of a day, newest first."""
    return session.scalars(select(TextVersion).where(TextVersion.kind == "digest", TextVersion.ref == day.isoformat())
                           .order_by(TextVersion.id.desc())).all()


def list_digests(session: Session, limit: int = 7) -> list:
    return session.scalars(select(Digest).order_by(Digest.day.desc()).limit(min(max(limit, 1), 60))).all()


def digest_neighbours(session: Session, day: dt.date) -> tuple:
    """(older, newer): the days of the nearest saved overviews before and after `day`, or None."""
    older = session.scalar(select(func.max(Digest.day)).where(Digest.day < day))
    newer = session.scalar(select(func.min(Digest.day)).where(Digest.day > day))
    return older, newer


def get_digest(session: Session, day) -> Optional[Digest]:
    return session.scalar(select(Digest).where(Digest.day == _date(day, "day")))


# --- which recordings the routine has handled --------------------------------------------------------
def mark_analyzed(session: Session, now: dt.datetime, recording_id: int) -> bool:
    """Mark a recording as handled. Returns False if it already was."""
    _visible_recording(session, recording_id)
    rec = session.get(Recording, recording_id)
    if rec.analyzed_at is not None:
        return False
    rec.analyzed_at = now
    session.commit()
    log.info("Recording marked analyzed id=%s", recording_id)
    return True


def unmark_analyzed(session: Session, recording_id: int) -> bool:
    """Owner only (no MCP tool): let the routine look at the recording again. False if it was not marked."""
    _visible_recording(session, recording_id)
    rec = session.get(Recording, recording_id)
    if rec.analyzed_at is None:
        return False
    rec.analyzed_at = None
    session.commit()
    log.info("Recording analyzed mark removed id=%s", recording_id)
    return True
