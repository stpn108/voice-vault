"""
Deletion at Plaud (REQ-001, D-003): trash, then permanent delete, each only when
every condition holds. Doubt means no deletion. With PLAUD_DELETE_ENABLED=false
(shadow mode) the decisions are only logged.
"""
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import Config
from database import Recording
from import_service import ImportStats
from plaud_client import PlaudAuthError, PlaudClient, PlaudError
from utils import as_utc, now_utc

log = logging.getLogger(__name__)


@dataclass
class DeletionStats:
    trashed: int = 0
    deleted: int = 0
    held_back: int = 0
    shadow: int = 0
    failed: int = 0
    assumed_trashed: int = 0


def _is_verified(rec: Recording) -> bool:
    return rec.verified_at is not None and rec.verified_hash == rec.content_hash


def unmet_trash_conditions(rec: Recording, now: dt.datetime, cfg: Config) -> list[str]:
    """Reasons a recording may not go to the Plaud trash yet; empty means it may."""
    reasons = []
    if rec.duration_ms <= 0:
        # Plaud may list a recording before its length is known; the age is then meaningless.
        reasons.append("duration_unknown")
    if now - as_utc(rec.ended_at) < dt.timedelta(minutes=cfg.min_age_minutes):
        reasons.append("too_young")
    if rec.discarded_at is not None:
        # The owner discarded it in the UI (D-009): the content is gone on purpose, so the
        # processing, verification and stability gates make no sense. Age and duration stay.
        return reasons
    if not rec.is_plaud_processed:
        reasons.append("plaud_not_processed")
    if not _is_verified(rec):
        reasons.append("import_not_verified")
    if now - as_utc(rec.last_changed_at) < dt.timedelta(minutes=cfg.stability_minutes):
        reasons.append("content_not_stable")
    return reasons


def may_delete_permanently(rec: Recording, now: dt.datetime, cfg: Config) -> bool:
    if rec.trashed_at is None or rec.deleted_at is not None:
        return False
    if rec.discarded_at is None and not _is_verified(rec):
        return False
    return now - as_utc(rec.trashed_at) >= dt.timedelta(hours=cfg.permanent_delete_after_hours)


def run_deletion(session_factory: Callable[[], Session], client: PlaudClient, cfg: Config,
                 now_fn: Callable[[], dt.datetime] = now_utc,
                 imported: Optional[ImportStats] = None) -> DeletionStats:
    """Trash and delete. The scheduled cycle always passes what the import of the same cycle saw
    (D-019): a recording is trashed only if Plaud listed it, it was read in full and verified just now.
    Without `imported` the decision rests on the database alone (direct calls in tests)."""
    stats = DeletionStats()
    with session_factory() as session:
        now = now_fn()
        pending = session.scalars(
            select(Recording).where(Recording.trashed_at.is_(None))
        ).all()
        listing_usable = imported is None or bool(imported.seen)
        for rec in pending:
            reasons = unmet_trash_conditions(rec, now, cfg)
            if not reasons and imported is not None:
                if not listing_usable:
                    reasons.append("plaud_list_empty")
                elif rec.plaud_id not in imported.seen:
                    # Not listed outside the trash any more: it was probably trashed already and the
                    # answer got lost. Remember that, so the permanent delete can follow; never call Plaud.
                    if cfg.delete_enabled:
                        rec.trashed_at = now
                        session.commit()
                        stats.assumed_trashed += 1
                        log.warning("Not in the Plaud list any more, treating as trashed plaud_id=%s", rec.plaud_id)
                    else:
                        stats.shadow += 1
                        log.info("SHADOW would treat as trashed (not listed) plaud_id=%s", rec.plaud_id)
                    continue
                elif rec.discarded_at is None and rec.plaud_id not in imported.confirmed:
                    reasons.append("not_confirmed_this_cycle")
            if reasons:
                stats.held_back += 1
                log.info("Not trashing plaud_id=%s reasons=%s", rec.plaud_id, ",".join(reasons))
                continue
            if stats.trashed >= cfg.max_trash_per_cycle:
                stats.held_back += 1
                log.warning("Trash limit per cycle reached (%d), the rest waits for the next cycle",
                            cfg.max_trash_per_cycle)
                break
            if not cfg.delete_enabled:
                stats.shadow += 1
                log.info("SHADOW would trash plaud_id=%s", rec.plaud_id)
                continue
            try:
                client.trash([rec.plaud_id])
            except PlaudAuthError:
                raise
            except PlaudError as exc:
                stats.failed += 1
                log.error("Trash failed plaud_id=%s reason=%s", rec.plaud_id, exc)
                continue
            rec.trashed_at = now
            session.commit()
            stats.trashed += 1
            log.info("Trashed at Plaud plaud_id=%s", rec.plaud_id)

        trashed = session.scalars(
            select(Recording).where(Recording.trashed_at.is_not(None), Recording.deleted_at.is_(None))
        ).all()
        for rec in trashed:
            if not may_delete_permanently(rec, now, cfg):
                continue
            if not cfg.delete_enabled:
                stats.shadow += 1
                log.info("SHADOW would delete permanently plaud_id=%s", rec.plaud_id)
                continue
            try:
                client.delete_permanently([rec.plaud_id])
            except PlaudAuthError:
                raise
            except PlaudError as exc:
                stats.failed += 1
                log.error("Permanent delete failed plaud_id=%s reason=%s", rec.plaud_id, exc)
                continue
            rec.deleted_at = now
            session.commit()
            stats.deleted += 1
            log.info("Deleted permanently at Plaud plaud_id=%s", rec.plaud_id)
    log.info("Deletion done %s", stats)
    return stats
