"""Owner-only care of the people names (D-034): delete a name, rename or merge it, and give the
unnamed speakers of a transcript a name.

Kept apart from todo_service, like topic_admin: only the web UI imports this module. Task changes go
through todo_service.update_todo, so each one is logged and can be undone on the task page. Speaker
labels of stored transcripts are changed in place; Plaud is not touched.
"""
import datetime as dt
import logging
import re
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import todo_service
from database import Recording, Segment, Todo
from import_service import UNNAMED_SPEAKER

log = logging.getLogger(__name__)

ORPHAN_RECORDINGS_MAX = 50
SAMPLES_PER_LABEL = 2
_UNKNOWN_NUMBER = re.compile(r"^unbekannt\s+(\d+)$", re.IGNORECASE)


class PeopleError(ValueError):
    """A rejected request. The message is safe to show."""


@dataclass
class PersonRow:
    name: str
    tasks: int = 0
    recordings: int = 0
    segments: int = 0


@dataclass
class OrphanLabel:
    recording_id: int
    title: str
    started_at: dt.datetime
    label: str
    segments: int
    samples: list


def _clean(name: str) -> str:
    if not isinstance(name, str):
        raise PeopleError("name must be text")
    name = " ".join(name.split())
    if not name:
        raise PeopleError("name must not be empty")
    if len(name) > todo_service.PERSON_MAX:
        raise PeopleError(f"name must have at most {todo_service.PERSON_MAX} characters")
    if UNNAMED_SPEAKER.match(name):
        raise PeopleError(f"'{name}' looks like a placeholder, not a name")
    return name


def _named(label: str) -> bool:
    return bool(label) and not UNNAMED_SPEAKER.match(label)


def _kept_segments():
    return select(Segment).join(Recording, Recording.id == Segment.recording_id).where(Recording.discarded_at.is_(None))


def people(session: Session) -> list:
    """Every name on a task or as a speaker of a kept recording, with how often it is used."""
    rows: dict = {}

    def row(name: str) -> PersonRow:
        return rows.setdefault(name.lower(), PersonRow(name))

    stmt = (select(Segment.speaker, func.count(), func.count(func.distinct(Segment.recording_id)))
            .join(Recording, Recording.id == Segment.recording_id).where(Recording.discarded_at.is_(None))
            .group_by(Segment.speaker))
    for speaker, count, recordings in session.execute(stmt):
        if _named(speaker):
            entry = row(" ".join(speaker.split()))
            entry.segments += count
            entry.recordings += recordings
    for stored in session.scalars(select(Todo.people).where(Todo.people != "")):
        for name in stored.split("\n"):
            if name:
                row(name).tasks += 1
    return sorted(rows.values(), key=lambda r: r.name.lower())


def person(session: Session, name: str):
    """The row of one name (any case) or None."""
    wanted = " ".join((name or "").split()).lower()
    return next((r for r in people(session) if r.name.lower() == wanted), None)


def _tasks_with(session: Session, name: str) -> list:
    line = "\n" + name.lower() + "\n"
    return session.scalars(select(Todo).where(
        func.lower("\n" + Todo.people + "\n").contains(line, autoescape=True))).all()


def _segments_matching(name: str):
    return func.lower(func.trim(Segment.speaker)) == name.lower()


def _relabel(session: Session, old: str, new_label_for) -> int:
    """Give every segment of a kept recording labelled `old` the label new_label_for(recording_id)."""
    ids = session.scalars(select(Segment.recording_id).join(Recording, Recording.id == Segment.recording_id)
                          .where(Recording.discarded_at.is_(None), _segments_matching(old)).distinct()).all()
    changed = 0
    for recording_id in ids:
        result = session.execute(update(Segment).where(Segment.recording_id == recording_id, _segments_matching(old))
                                 .values(speaker=new_label_for(recording_id)))
        changed += result.rowcount
    return changed


def _next_unknown(session: Session, recording_id: int) -> str:
    labels = session.scalars(select(Segment.speaker).where(Segment.recording_id == recording_id).distinct()).all()
    used = [int(m.group(1)) for m in (_UNKNOWN_NUMBER.match(label.strip()) for label in labels) if m]
    return f"Unbekannt {max(used, default=0) + 1}"


def delete_person(session: Session, now: dt.datetime, name: str) -> dict:
    """Take a name off every task; its transcript passages become "Unbekannt N" (one number per recording)."""
    found = person(session, name)
    if found is None:
        raise PeopleError(f"unknown name '{name}'")
    tasks = _tasks_with(session, found.name)
    for todo in tasks:
        current = todo_service.get_todo(session, now, todo.id)[0].people
        todo_service.update_todo(session, now, todo.id, actor="owner", note=f"name '{found.name}' deleted",
                                 people=[n for n in current if n.lower() != found.name.lower()])
    segments = _relabel(session, found.name, lambda recording_id: _next_unknown(session, recording_id))
    session.commit()
    log.info("Person deleted tasks=%s segments=%s", len(tasks), segments)
    return {"name": found.name, "tasks": len(tasks), "segments": segments}


def rename_person(session: Session, now: dt.datetime, old: str, new: str) -> dict:
    """Rename a name everywhere; onto an existing name this merges the two."""
    found = person(session, old)
    if found is None:
        raise PeopleError(f"unknown name '{old}'")
    new = _clean(new)
    if new == found.name:
        return {"name": new, "tasks": 0, "segments": 0}
    tasks = _tasks_with(session, found.name)
    for todo in tasks:
        current = todo_service.get_todo(session, now, todo.id)[0].people
        renamed = [new if n.lower() == found.name.lower() else n for n in current]
        todo_service.update_todo(session, now, todo.id, actor="owner", note=f"'{found.name}' renamed to '{new}'",
                                 people=renamed)
    segments = _relabel(session, found.name, lambda recording_id: new)
    session.commit()
    log.info("Person renamed tasks=%s segments=%s", len(tasks), segments)
    return {"name": new, "tasks": len(tasks), "segments": segments}


def orphans(session: Session) -> list:
    """Unnamed speaker labels of kept recordings, newest recording first, with a few text samples."""
    stmt = (select(Recording.id, Recording.title, Recording.started_at, Segment.speaker, func.count())
            .join(Segment, Segment.recording_id == Recording.id).where(Recording.discarded_at.is_(None))
            .group_by(Recording.id, Recording.title, Recording.started_at, Segment.speaker)
            .order_by(Recording.started_at.desc(), Recording.id.desc(), Segment.speaker))
    found, recordings = [], []
    for recording_id, title, started_at, speaker, count in session.execute(stmt):
        if _named(speaker):
            continue
        if recording_id not in recordings:
            if len(recordings) == ORPHAN_RECORDINGS_MAX:
                break
            recordings.append(recording_id)
        samples = session.scalars(select(Segment.text).where(Segment.recording_id == recording_id,
                                                             Segment.speaker == speaker, Segment.text != "")
                                  .order_by(func.length(Segment.text).desc(), Segment.idx)
                                  .limit(SAMPLES_PER_LABEL)).all()
        found.append(OrphanLabel(recording_id, title, started_at, speaker, count, samples))
    return found


def assign_speaker(session: Session, recording_id: int, label: str, name: str) -> int:
    """Give the unnamed speaker `label` of one recording a name. Returns the passages changed."""
    name = _clean(name)
    rec = session.get(Recording, recording_id)
    if rec is None or rec.discarded_at is not None:
        raise PeopleError(f"no recording with id {recording_id}")
    if _named(label):
        raise PeopleError("this speaker has a name already; rename it in the list of names")
    result = session.execute(update(Segment).where(Segment.recording_id == recording_id, Segment.speaker == label)
                             .values(speaker=name))
    if not result.rowcount:
        raise PeopleError("this speaker has no passages in the recording (any more)")
    session.commit()
    log.info("Speaker named recording=%s segments=%s", recording_id, result.rowcount)
    return result.rowcount
