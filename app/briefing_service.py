"""Briefing on one person before a meeting (D-038): open tasks with them, what they owe the owner,
shared topics with their latest note, and the latest conversations they spoke in. Read only.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import todo_service as todos
from database import Recording, Segment, Topic, TopicNote
from utils import as_utc

RECORDINGS_SHOWN = 3
RECORDINGS_FOR_TOPICS = 10
TOPICS_SHOWN = 8


@dataclass
class Briefing:
    name: str
    mine: list
    waiting: list
    topics: list = field(default_factory=list)       # (topic, latest note or None)
    recordings: list = field(default_factory=list)   # newest first
    conversations: int = 0
    last_talk: Optional[dt.datetime] = None


def _clean(name: str) -> str:
    return " ".join((name or "").split())


def briefing(session: Session, now: dt.datetime, name: str) -> Optional[Briefing]:
    name = _clean(name)
    if not name or len(name) > todos.PERSON_MAX:
        return None
    mine = todos.list_todos(session, now, status="open", person=name, kind="mine", limit=todos.LIST_LIMIT_MAX)[0]
    waiting = todos.list_todos(session, now, status="open", person=name, kind="waiting", limit=todos.LIST_LIMIT_MAX)[0]
    spoke = (select(Segment.recording_id).where(func.lower(func.trim(Segment.speaker)) == name.lower()).distinct())
    recordings = session.scalars(select(Recording).where(Recording.id.in_(spoke), Recording.discarded_at.is_(None))
                                 .order_by(Recording.started_at.desc(), Recording.id.desc())).all()
    if not (mine or waiting or recordings):
        return None
    spelled = next((p for row in mine + waiting for p in row.people if p.lower() == name.lower()), None)
    if spelled is None and recordings:
        spelled = next((_clean(s) for s in session.scalars(select(Segment.speaker).where(
            Segment.recording_id == recordings[0].id, func.lower(func.trim(Segment.speaker)) == name.lower()))), name)

    hidden = set(todos._excluded_ids(session))
    topic_ids = [row.topic_id for row in mine + waiting if row.topic_id]
    recent = [r.id for r in recordings[:RECORDINGS_FOR_TOPICS]]
    if recent:
        topic_ids += session.scalars(select(TopicNote.topic_id).where(TopicNote.recording_id.in_(recent))).all()
    topics = []
    for topic_id in dict.fromkeys(topic_ids):
        topic = session.get(Topic, topic_id)
        if topic is None or topic.id in hidden:
            continue
        note = session.scalars(select(TopicNote).join(Recording, Recording.id == TopicNote.recording_id)
                               .where(TopicNote.topic_id == topic.id)
                               .order_by(Recording.started_at.desc(), TopicNote.id.desc()).limit(1)).first()
        topics.append((topic, note))
    topics.sort(key=lambda pair: as_utc(pair[1].updated_at) if pair[1] else as_utc(pair[0].created_at), reverse=True)

    return Briefing(name=spelled or name, mine=mine, waiting=waiting, topics=topics[:TOPICS_SHOWN],
                    recordings=recordings[:RECORDINGS_SHOWN], conversations=len(recordings),
                    last_talk=as_utc(recordings[0].started_at) if recordings else None)
