"""Briefing on one person before a meeting (D-038): open tasks with them, what they owe the owner,
shared topics with their latest note, and the latest conversations they spoke in. And the same in short
for all people at once, the dashboard (REQ-010). Read only.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import todo_service as todos
from database import PersonProfile, Recording, Segment, Todo, Topic, TopicNote
from utils import as_utc

RECORDINGS_SHOWN = 3
RECORDINGS_FOR_TOPICS = 10
TOPICS_SHOWN = 8
OVERVIEW_SORTS = ("recent", "open", "name")


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

    import person_service  # the same rules as the profile (D-059): hidden topics, 20 % share of a talk
    hidden = set(todos._excluded_ids(session)) | person_service.hidden_topics(session, name)
    topic_ids = [row.topic_id for row in mine + waiting if row.topic_id]
    counted = set(person_service.talked_in(session, name))
    recent = [r.id for r in recordings if r.id in counted][:RECORDINGS_FOR_TOPICS]
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


# --- all people at once (REQ-010, D-077) ---------------------------------------------------------------
@dataclass
class PersonCard:
    name: str
    role: str = ""
    next_points: str = ""
    agreements: list = field(default_factory=list)
    open: int = 0          # the owner's tasks with this person that are not started
    in_progress: int = 0   # ... and the ones he is working on
    waiting: int = 0       # open promises of this person to the owner
    overdue: int = 0       # of these tasks and promises, past their due date
    conversations: int = 0
    last_talk: Optional[dt.datetime] = None
    days_since: Optional[int] = None


def overview(session: Session, now: dt.datetime, today: dt.date, sort: str = "recent", q: str = "") -> list:
    """One PersonCard per known person (not excluded), counted like `briefing` counts for one: tasks and
    promises that are unfinished and not in an excluded topic, conversations in recordings still kept.
    `q` keeps the people whose name contains it, whatever the case."""
    if sort not in OVERVIEW_SORTS:
        raise ValueError(f"sort must be one of: {', '.join(OVERVIEW_SORTS)}")
    import person_service
    wanted = _clean(q).lower()
    cards = {name.lower(): PersonCard(name) for name in todos.known_people(session) if wanted in name.lower()}
    stmt = select(Todo.kind, Todo.status, Todo.people, Todo.due_date).where(Todo.status.in_(todos.ACTIVE), Todo.people != "")
    hidden = todos._excluded_ids(session)
    if hidden:
        stmt = stmt.where(Todo.topic_id.is_(None) | Todo.topic_id.not_in(hidden))
    for kind, status, people, due in session.execute(stmt):
        for key in {n.lower() for n in people.split("\n") if n}:
            card = cards.get(key)
            if card is None:
                continue
            if kind == "waiting":
                card.waiting += 1
            elif status == "in_progress":
                card.in_progress += 1
            else:
                card.open += 1
            card.overdue += bool(due and due < today)
    spoke = session.execute(select(Segment.speaker, Recording.id, Recording.started_at)
                            .join(Recording, Recording.id == Segment.recording_id)
                            .where(Recording.discarded_at.is_(None)).distinct())
    talks = {}
    for speaker, recording_id, started in spoke:
        talks.setdefault(_clean(speaker).lower(), {})[recording_id] = as_utc(started)
    for key, card in cards.items():
        mine = talks.get(key)
        if mine:
            card.conversations, card.last_talk = len(mine), max(mine.values())
            card.days_since = (now - card.last_talk).days
    for found in session.scalars(select(PersonProfile)):
        card = cards.get(found.name.lower())
        if card is not None:
            card.role, card.next_points = found.role or "", found.next_points or ""
            card.agreements = person_service.agreement_list(found)
    rows = sorted(cards.values(), key=lambda card: card.name.lower())
    if sort == "open":
        rows.sort(key=lambda card: -(card.open + card.in_progress + card.waiting))
    elif sort == "recent":
        rows.sort(key=lambda card: card.last_talk.timestamp() if card.last_talk else float("-inf"), reverse=True)
    return rows
