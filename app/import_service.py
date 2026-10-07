"""
Import (REQ-001): copy processed Plaud recordings into the database and verify
the stored copy by reading it back.
"""
import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Recording, Segment
from plaud_client import MIN_PLAUSIBLE_START_MS, PlaudAuthError, PlaudClient, PlaudError, PlaudRecording
from utils import now_utc, from_epoch_ms

log = logging.getLogger(__name__)


@dataclass
class ImportStats:
    imported: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    verified: int = 0
    # What this cycle saw at Plaud and verified; the deletion step trashes nothing else (D-019).
    seen: set = field(default_factory=set)
    confirmed: set = field(default_factory=set)


def clean_text(value) -> str:
    """Text the database and the hash can take: no NUL characters, no lone surrogates."""
    return str(value).replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def normalize_segments(raw_segments: list[dict]) -> list[dict]:
    """Reduce Plaud utterances to the fields we store."""
    return [
        {
            "speaker": clean_text(s.get("speaker") or s.get("original_speaker") or "")[:255],
            "start_ms": _int_or_none(s.get("start_time")),
            "end_ms": _int_or_none(s.get("end_time")),
            "text": clean_text(s.get("content") or ""),
        }
        for s in raw_segments
    ]


def _int_or_none(value):
    """A whole number of milliseconds that fits the column; anything else (NaN, huge) is unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (value != value or abs(value) == float("inf")):
        return None
    number = int(value)
    return number if 0 <= number < 2**31 else None


def has_content(summary: str, segments: list[dict]) -> bool:
    """A summary and at least one utterance with real text; empty strings are not a transcript."""
    return bool(summary.strip()) and any(s["text"].strip() for s in segments)


def looks_truncated(segments: list[dict], duration_ms: int) -> bool:
    """A long recording whose transcript ends in its first third: probably cut off."""
    ends = [s["end_ms"] for s in segments if s["end_ms"] is not None]
    return bool(ends) and duration_ms >= 120_000 and max(ends) < duration_ms * 0.3


def compute_content_hash(summary: str, segments: list[dict]) -> str:
    """The single definition of "same content": summary plus normalized segments."""
    blob = json.dumps({"summary": summary, "segments": segments}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stored_segments(session: Session, recording_id: int) -> list[dict]:
    rows = session.scalars(
        select(Segment).where(Segment.recording_id == recording_id).order_by(Segment.idx)
    ).all()
    return [
        {"speaker": r.speaker, "start_ms": r.start_ms, "end_ms": r.end_ms, "text": r.text}
        for r in rows
    ]


def verify_import(session: Session, plaud_id: str, expected_hash: str, expected_count: int,
                  now: dt.datetime) -> bool:
    """Commit, drop cached state, read the rows back and compare hash and segment count."""
    session.commit()
    session.expire_all()
    rec = session.scalar(select(Recording).where(Recording.plaud_id == plaud_id))
    if rec is None:
        log.error("Verification failed plaud_id=%s reason=row_missing", plaud_id)
        return False
    stored = _stored_segments(session, rec.id)
    stored_hash = compute_content_hash(rec.summary, stored)
    if not stored or not rec.summary or len(stored) != expected_count or stored_hash != expected_hash:
        log.error(
            "Verification failed plaud_id=%s segments_stored=%d segments_expected=%d hash_match=%s",
            plaud_id, len(stored), expected_count, stored_hash == expected_hash,
        )
        return False
    rec.verified_hash = stored_hash
    rec.verified_at = now
    session.commit()
    return True


def import_recording(session: Session, client: PlaudClient, item: PlaudRecording,
                     now: dt.datetime, stats: ImportStats) -> None:
    existing = session.scalar(select(Recording).where(Recording.plaud_id == item.plaud_id))
    if existing is not None and existing.trashed_at is not None and existing.deleted_at is None:
        # Listed outside the Plaud trash again: the owner restored it there. It must not be deleted for good.
        existing.trashed_at = None
        session.commit()
        log.warning("Recording is back outside the Plaud trash, permanent delete cancelled plaud_id=%s", item.plaud_id)
    if existing is not None and existing.discarded_at is not None:
        # Discarded in the UI (REQ-002): never fetch or store it again.
        stats.skipped += 1
        return
    detail = client.get_detail(item.plaud_id)
    if not detail.is_processed:
        if existing is not None and existing.is_plaud_processed:
            existing.is_plaud_processed = False
            session.commit()
        stats.skipped += 1
        log.info("Recording not processed yet plaud_id=%s", item.plaud_id)
        return

    raw_segments = client.fetch_segments(detail)
    summary = client.fetch_summary(detail)
    summary = clean_text(summary)
    segments = normalize_segments(raw_segments)
    if not has_content(summary, segments):
        # Doubt means no import and therefore no deletion (D-003). Empty strings count as empty:
        # a renamed key in Plaud's answer must not look like a finished transcript.
        stats.skipped += 1
        log.warning(
            "Recording processed but transcript or summary has no text plaud_id=%s segments=%d summary_chars=%d",
            item.plaud_id, len(segments), len(summary.strip()),
        )
        return

    start_ms = detail.start_ms or item.start_ms
    if start_ms < MIN_PLAUSIBLE_START_MS or start_ms > (now.timestamp() + 86400) * 1000:
        # 0 or garbage would make the recording look decades old and pass the age gate at once.
        raise PlaudError(f"implausible start time for {item.plaud_id}")
    content_hash = compute_content_hash(summary, segments)
    started = from_epoch_ms(start_ms)
    duration = detail.duration_ms or item.duration_ms
    ended = started + dt.timedelta(milliseconds=duration)
    title = detail.title or item.title

    if existing is None:
        rec = Recording(
            plaud_id=item.plaud_id, title=title, started_at=started, ended_at=ended,
            duration_ms=duration, summary=summary, segment_count=len(segments),
            content_hash=content_hash, is_plaud_processed=True, last_changed_at=now,
        )
        session.add(rec)
        session.flush()
        _replace_segments(session, rec.id, segments)
        stats.imported += 1
    elif existing.content_hash != content_hash:
        existing.title, existing.started_at, existing.ended_at = title, started, ended
        existing.duration_ms, existing.summary = duration, summary
        existing.segment_count, existing.content_hash = len(segments), content_hash
        existing.is_plaud_processed, existing.last_changed_at = True, now
        existing.verified_hash = existing.verified_at = None
        _replace_segments(session, existing.id, segments)
        stats.updated += 1
        log.info("Recording changed at Plaud, re-imported plaud_id=%s", item.plaud_id)
    else:
        # Same content: metadata may still be corrected (e.g. a duration that was 0 while
        # the recording was uploading). This does not restart the stability window.
        existing.title, existing.is_plaud_processed = title, True
        existing.started_at, existing.ended_at, existing.duration_ms = started, ended, duration
        stats.unchanged += 1

    session.commit()
    suspicious = looks_truncated(segments, duration)
    if suspicious:
        # Stored for reading, but never verified and therefore never deleted at Plaud.
        log.warning("Transcript ends far before the recording does, not verifying plaud_id=%s", item.plaud_id)
    current = session.scalar(select(Recording).where(Recording.plaud_id == item.plaud_id))
    if not suspicious and current.verified_hash != content_hash:
        if verify_import(session, item.plaud_id, content_hash, len(segments), now):
            stats.verified += 1
    final = session.scalar(select(Recording).where(Recording.plaud_id == item.plaud_id))
    if not suspicious and final.verified_hash == content_hash:
        stats.confirmed.add(item.plaud_id)


def _replace_segments(session: Session, recording_id: int, segments: list[dict]) -> None:
    session.query(Segment).filter(Segment.recording_id == recording_id).delete()
    session.add_all(
        Segment(recording_id=recording_id, idx=i, **seg) for i, seg in enumerate(segments)
    )


def run_import(session_factory: Callable[[], Session], client: PlaudClient,
               now_fn: Callable[[], dt.datetime] = now_utc) -> ImportStats:
    """Import every recording still at Plaud. A PlaudAuthError aborts the run."""
    stats = ImportStats()
    items = client.list_recordings()
    stats.seen = {item.plaud_id for item in items}
    log.info("Plaud list recordings=%d", len(items))
    for item in items:
        with session_factory() as session:
            try:
                import_recording(session, client, item, now_fn(), stats)
            except PlaudAuthError:
                raise
            except PlaudError as exc:
                session.rollback()
                stats.failed += 1
                log.error("Import failed plaud_id=%s reason=%s", item.plaud_id, exc)
            except Exception as exc:  # noqa: BLE001 - one odd recording must not stop the others
                session.rollback()
                stats.failed += 1
                log.error("Import failed plaud_id=%s reason=unexpected %s", item.plaud_id, type(exc).__name__)
    log.info("Import done %s", stats)
    return stats
