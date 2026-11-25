"""
Deleting all data in one go (REQ-011, D-079). Every kept recording is discarded the way "Verwerfen" does it,
so its row stays as a tombstone: the import does not fetch it again and the deletion at Plaud goes on.
Everything built from the recordings is deleted. The Plaud login, the OAuth tokens, the sync requests and
the analysis log stay, so the import keeps running.
"""
import datetime as dt
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import recording_service
from database import (Digest, ExcludedPerson, GlossaryAlias, GlossarySuggestion, GlossaryTerm, MindmapNode,
                      PersonMapNode, PersonProfile, PersonTopicHidden, PersonTopicNote, Recording, TextVersion, Todo,
                      TodoEvent, TodoLink, Topic, TopicNote)

log = logging.getLogger(__name__)

# Children before their parents, so the foreign keys hold on Postgres.
WIPED_TABLES = (TodoLink, TodoEvent, Todo, TopicNote, PersonTopicNote, PersonTopicHidden, Topic, MindmapNode,
                PersonMapNode, PersonProfile, ExcludedPerson, Digest, TextVersion, GlossaryAlias, GlossarySuggestion,
                GlossaryTerm)


def _kept_recordings():
    return select(Recording.id).where(Recording.discarded_at.is_(None))


def counts(session: Session) -> dict:
    """What a wipe takes: the kept recordings, then the rows of each wiped table by table name."""
    found = {"recordings": session.scalar(select(func.count()).select_from(_kept_recordings().subquery()))}
    for model in WIPED_TABLES:
        found[model.__tablename__] = session.scalar(select(func.count()).select_from(model))
    return found


def fingerprint(found: dict) -> str:
    """Names the exact numbers the owner looked at."""
    return recording_service.ids_fingerprint(list(found.values()))


def wipe_all(session: Session, now: dt.datetime) -> dict:
    """Delete every wiped table, then discard every kept recording. Returns what was there before."""
    found = counts(session)
    for model in WIPED_TABLES:
        session.execute(delete(model))
    session.commit()
    recording_service.discard_ids(session, sorted(session.scalars(_kept_recordings()).all()), now)
    log.info("All data deleted %s", " ".join(f"{name}={count}" for name, count in found.items()))
    return found
