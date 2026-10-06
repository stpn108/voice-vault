"""
Import (REQ-001): copy processed Plaud recordings into the database and verify
the stored copy by reading it back.
"""
import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import Recording, Segment
from plaud_client import PlaudClient, PlaudError, PlaudRecording
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


def normalize_segments(raw_segments: list[dict]) -> list[dict]:
    """Reduce Plaud utterances to the fields we store."""
    return [
        {
            "speaker": str(s.get("speaker") or s.get("original_speaker") or ""),
            "start_ms": _int_or_none(s.get("start_time")),
            "end_ms": _int_or_none(s.get("end_time")),
            "text": str(s.get("content") or ""),
        }
        for s in raw_segments
    ]


def _int_or_none(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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
    segments = normalize_segments(raw_segments)
    if not segments or not summary:
        # Doubt means no import and therefore no deletion (D-003).
        stats.skipped += 1
        log.warning(
            "Recording processed but transcript or summary is empty plaud_id=%s segments=%d summary_chars=%d",
            item.plaud_id, len(segments), len(summary),
        )
        return

    content_hash = compute_content_hash(summary, segments)
    started = from_epoch_ms(detail.start_ms or item.start_ms)
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
    current = session.scalar(select(Recording).where(Recording.plaud_id == item.plaud_id))
    if current.verified_hash != content_hash:
        if verify_import(session, item.plaud_id, content_hash, len(segments), now):
            stats.verified += 1


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
    log.info("Plaud list recordings=%d", len(items))
    for item in items:
        with session_factory() as session:
            try:
                import_recording(session, client, item, now_fn(), stats)
            except PlaudError as exc:
                from plaud_client import PlaudAuthError
                if isinstance(exc, PlaudAuthError):
                    raise
                session.rollback()
                stats.failed += 1
                log.error("Import failed plaud_id=%s reason=%s", item.plaud_id, exc)
    log.info("Import done %s", stats)
    return stats
