"""
Reading and discarding stored recordings for the web UI (REQ-002).
All queries are parameterised through SQLAlchemy; the UI never builds SQL.
"""
import base64
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from database import Recording, Segment
from utils import as_utc

log = logging.getLogger(__name__)

PAGE_SIZE = 50

STATE_WAITING = "waiting"
STATE_IMPORTED = "imported"
STATE_VERIFIED = "verified"
STATE_TRASHED = "trashed"
STATE_DELETED = "deleted"


@dataclass
class RecordingRow:
    id: int
    title: str
    started_at: dt.datetime
    duration_ms: int
    state: str
    excerpt: str = ""


@dataclass
class Page:
    rows: list
    next_cursor: Optional[str]


def recording_state(rec: Recording, now: dt.datetime, stability_minutes: int) -> str:
    """The one place that names the state of a stored recording."""
    if rec.deleted_at is not None:
        return STATE_DELETED
    if rec.trashed_at is not None:
        return STATE_TRASHED
    unstable = now - as_utc(rec.last_changed_at) < dt.timedelta(minutes=stability_minutes)
    if not rec.is_plaud_processed or unstable:
        return STATE_WAITING
    if rec.verified_at is not None and rec.verified_hash == rec.content_hash:
        return STATE_VERIFIED
    return STATE_IMPORTED


def encode_cursor(started_at: dt.datetime, recording_id: int) -> str:
    raw = f"{as_utc(started_at).isoformat()}|{recording_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple:
    """Return (started_at, id). Raises ValueError for anything that is not our cursor."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, rec_id = raw.split("|")
        return as_utc(dt.datetime.fromisoformat(stamp)), int(rec_id)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid cursor") from exc


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _visible():
    return Recording.discarded_at.is_(None)


EXCERPT_CHARS = 240


def list_recordings(session: Session, now: dt.datetime, stability_minutes: int,
                    query: str = "", cursor: Optional[str] = None, limit: int = PAGE_SIZE,
                    since: Optional[dt.datetime] = None, until: Optional[dt.datetime] = None) -> Page:
    """Newest first, keyset paging (no offset), optional case-insensitive search.

    `since` is inclusive, `until` exclusive, both compared with the start of the recording.
    """
    limit = min(max(limit, 1), PAGE_SIZE)
    stmt = select(Recording).where(_visible())
    if since is not None:
        stmt = stmt.where(Recording.started_at >= as_utc(since))
    if until is not None:
        stmt = stmt.where(Recording.started_at < as_utc(until))
    query = query.strip()
    if query:
        pattern = _like_pattern(query)
        in_segments = exists().where(
            Segment.recording_id == Recording.id, Segment.text.ilike(pattern, escape="\\")
        )
        stmt = stmt.where(or_(
            Recording.title.ilike(pattern, escape="\\"),
            Recording.summary.ilike(pattern, escape="\\"),
            in_segments,
        ))
    if cursor:
        started_at, rec_id = decode_cursor(cursor)
        stmt = stmt.where(or_(
            Recording.started_at < started_at,
            and_(Recording.started_at == started_at, Recording.id < rec_id),
        ))
    stmt = stmt.order_by(Recording.started_at.desc(), Recording.id.desc()).limit(limit + 1)
    found = session.scalars(stmt).all()
    page = found[:limit]
    next_cursor = encode_cursor(page[-1].started_at, page[-1].id) if len(found) > limit else None
    rows = [
        RecordingRow(r.id, r.title, as_utc(r.started_at), r.duration_ms,
                     recording_state(r, now, stability_minutes), r.summary[:EXCERPT_CHARS])
        for r in page
    ]
    return Page(rows, next_cursor)


def get_detail(session: Session, recording_id: int):
    """Return (recording, segments) or None if it does not exist or was discarded."""
    rec = session.get(Recording, recording_id)
    if rec is None or rec.discarded_at is not None:
        return None
    segments = session.scalars(
        select(Segment).where(Segment.recording_id == rec.id).order_by(Segment.idx)
    ).all()
    return rec, segments


def get_segments(session: Session, recording_id: int, offset: int, limit: int) -> list:
    """A slice of one recording's transcript, in order."""
    return session.scalars(
        select(Segment).where(Segment.recording_id == recording_id)
        .order_by(Segment.idx).offset(offset).limit(limit)
    ).all()


def discard(session: Session, recording_id: int, now: dt.datetime) -> bool:
    """Remove all content and keep a tombstone so the import never fetches it again.

    The hashes and verification stay: they record that the content was verified before
    it was discarded, which the deletion pipeline relies on for a recording already trashed.
    """
    rec = session.get(Recording, recording_id)
    if rec is None or rec.discarded_at is not None:
        return False
    session.query(Segment).filter(Segment.recording_id == rec.id).delete()
    rec.title = ""
    rec.summary = ""
    rec.segment_count = 0
    rec.discarded_at = now
    session.commit()
    log.info("Recording discarded plaud_id=%s", rec.plaud_id)
    return True


def _older_than(days: int, now: dt.datetime):
    return and_(_visible(), Recording.started_at < now - dt.timedelta(days=days))


def count_older_than(session: Session, days: int, now: dt.datetime) -> int:
    return len(session.scalars(select(Recording.id).where(_older_than(days, now))).all())


def discard_older_than(session: Session, days: int, now: dt.datetime) -> int:
    ids = session.scalars(select(Recording.id).where(_older_than(days, now))).all()
    return sum(1 for rec_id in ids if discard(session, rec_id, now))
