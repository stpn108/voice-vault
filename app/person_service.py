"""Living person profiles and topic clusters (D-057), and topics that probably belong together (D-058).

Claude (routine, MCP) and the owner (web UI) write here. Only work matters: role and unit, what someone
works on, where a shared topic stands. Every replaced text is kept in text_versions. Excluded people and
excluded topics get nothing. Nothing in this module deletes.
"""
import datetime as dt
import logging
from collections import Counter
from itertools import combinations
from typing import Optional
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import todo_service as todos
from database import PersonMapNode, PersonProfile, PersonTopicHidden, PersonTopicNote, Segment, Todo, TodoLink, Topic, TopicNote

log = logging.getLogger(__name__)

ROLE_MAX, WORK_MAX, POINTS_MAX, CLUSTER_MAX = 200, 800, 1000, 800
TALK_SHARE_MIN = 0.2  # share of a conversation's passages before it ties the person to its topics (D-059)
RELATION_MIN_SCORE = 2


class PersonError(ValueError):
    """A rejected request. The message is safe to show."""


def _key(name: str) -> str:
    return " ".join((name or "").split()).lower()


def _writable_person(session: Session, name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > todos.PERSON_MAX:
        raise PersonError(f"name must have 1 to {todos.PERSON_MAX} characters")
    if name.lower() in todos.excluded_people(session):
        raise PersonError("this person is excluded by the owner: record nothing about them")
    return name


def profile(session: Session, name: str) -> Optional[PersonProfile]:
    return session.scalar(select(PersonProfile).where(func.lower(PersonProfile.name) == _key(name)))


def update_profile(session: Session, now: dt.datetime, name: str, *, actor: str, role=None, current_work=None,
                   next_points=None) -> PersonProfile:
    """Change the given fields of a profile (None leaves a field as it is). Old texts are kept."""
    actor = todos._actor(actor)
    name = _writable_person(session, name)
    found = profile(session, name)
    if found is None:
        found = PersonProfile(name=name, role="", current_work="", next_points="", updated_at=now)
        session.add(found)
        session.flush()
    for field, value, limit in (("role", role, ROLE_MAX), ("current_work", current_work, WORK_MAX),
                                ("next_points", next_points, POINTS_MAX)):
        if value is None:
            continue
        value = todos._text(value, field, limit)
        old = getattr(found, field) or ""
        if value != old:
            if old:
                todos._remember(session, "person", f"{found.id}:{field}"[:64], old, now)
            setattr(found, field, value)
            found.updated_by, found.updated_at = actor, now
    session.commit()
    log.info("Person profile updated id=%s actor=%s", found.id, actor)
    return found


def set_topic_note(session: Session, now: dt.datetime, name: str, topic: str, note: str, *, actor: str,
                   recording_id: Optional[int] = None) -> PersonTopicNote:
    """Where a topic stands with this person; a second call replaces the note (the old one is kept)."""
    actor = todos._actor(actor)
    name = _writable_person(session, name)
    note = todos._text(note, "note", CLUSTER_MAX, required=True)
    recording_id = todos._visible_recording(session, recording_id)
    topic_row = todos._writable_topic(session, topic)
    if topic_row.id in hidden_topics(session, name):
        raise PersonError(f"the owner took topic '{topic_row.name}' off {name}; do not record it again")
    found = session.scalar(select(PersonTopicNote).where(PersonTopicNote.person == name.lower(),
                                                         PersonTopicNote.topic_id == topic_row.id))
    if found is None:
        found = PersonTopicNote(person=name.lower(), topic_id=topic_row.id, note=note, recording_id=recording_id,
                                updated_by=actor, updated_at=now)
        session.add(found)
        if profile(session, name) is None:
            session.add(PersonProfile(name=name, role="", current_work="", next_points="", updated_by=actor,
                                      updated_at=now))
    elif found.note != note or recording_id:
        if found.note != note:
            todos._remember(session, "person_topic", str(found.id), found.note, now)
        found.note, found.updated_by, found.updated_at = note, actor, now
        found.recording_id = recording_id or found.recording_id
    session.commit()
    return found


def hidden_topics(session: Session, name: str) -> set:
    """Topics the owner took off this person (D-059)."""
    return set(session.scalars(select(PersonTopicHidden.topic_id).where(PersonTopicHidden.person == _key(name))))


def clusters(session: Session, name: str) -> list:
    """(topic, note) of this person's topics, newest first; excluded and hidden topics left out."""
    hidden = set(todos._excluded_ids(session)) | hidden_topics(session, name)
    rows = session.execute(select(Topic, PersonTopicNote).join(PersonTopicNote, PersonTopicNote.topic_id == Topic.id)
                           .where(PersonTopicNote.person == _key(name))
                           .order_by(PersonTopicNote.updated_at.desc())).all()
    return [(topic, note) for topic, note in rows if topic.id not in hidden]


def talked_in(session: Session, name: str) -> list:
    """Recordings in which the person spoke at least TALK_SHARE_MIN of the passages (D-059)."""
    own = dict(session.execute(select(Segment.recording_id, func.count()).where(
        func.lower(func.trim(Segment.speaker)) == _key(name)).group_by(Segment.recording_id)).all())
    if not own:
        return []
    total = dict(session.execute(select(Segment.recording_id, func.count()).where(Segment.recording_id.in_(list(own)))
                                 .group_by(Segment.recording_id)).all())
    return [rid for rid, count in own.items() if count >= TALK_SHARE_MIN * total[rid]]


def person_topics(session: Session, name: str) -> list:
    """Every topic connected to the person: clusters, topics of tasks with them, topics of their talks."""
    hidden = set(todos._excluded_ids(session)) | hidden_topics(session, name)
    key = _key(name)
    ids = [t.id for t, _ in clusters(session, name)]
    for todo in session.scalars(select(Todo).where(Todo.topic_id.is_not(None), Todo.status.in_(todos.ACTIVE))):
        if key in {p.lower() for p in (todo.people or "").split("\n") if p}:
            ids.append(todo.topic_id)
    ids += session.scalars(select(TopicNote.topic_id).where(TopicNote.recording_id.in_(talked_in(session, name)))).all()
    return [i for i in dict.fromkeys(ids) if i not in hidden]


# --- topics that probably belong together (D-058) ---------------------------------------------------
def topic_relations(session: Session, topic_ids: list, ignore: str = "") -> list:
    """(topic a, topic b, score, reasons) for pairs among `topic_ids` that are not obviously unrelated.

    Counted, not guessed: shared conversations (topic notes of the same recording), linked tasks across the
    two topics, and people who are on both topics (not `ignore`, the person the map is about: they are on all of
    them). Pairs below RELATION_MIN_SCORE are left out."""
    wanted = set(topic_ids)
    talks, links, people = Counter(), Counter(), Counter()
    by_recording = {}
    for note in session.scalars(select(TopicNote).where(TopicNote.topic_id.in_(wanted))):
        by_recording.setdefault(note.recording_id, set()).add(note.topic_id)
    for group in by_recording.values():
        for pair in combinations(sorted(group), 2):
            talks[pair] += 1
    topic_of = {t.id: t.topic_id for t in session.scalars(select(Todo).where(Todo.topic_id.in_(wanted)))}
    for link in session.scalars(select(TodoLink)):
        a, b = topic_of.get(link.low_id), topic_of.get(link.high_id)
        if a and b and a != b:
            links[tuple(sorted((a, b)))] += 1
    topics_of_person = {}
    for todo in session.scalars(select(Todo).where(Todo.topic_id.in_(wanted))):
        for name in (todo.people or "").split("\n"):
            if name:
                topics_of_person.setdefault(name.lower(), set()).add(todo.topic_id)
    for note in session.scalars(select(PersonTopicNote).where(PersonTopicNote.topic_id.in_(wanted))):
        topics_of_person.setdefault(note.person, set()).add(note.topic_id)
    for pair in session.scalars(select(PersonTopicHidden)):
        topics_of_person.get(pair.person, set()).discard(pair.topic_id)
    topics_of_person.pop(ignore.lower(), None)
    for group in topics_of_person.values():
        for pair in combinations(sorted(group), 2):
            people[pair] += 1
    result = []
    for pair in set(talks) | set(links) | set(people):
        score = 2 * talks[pair] + 2 * links[pair] + people[pair]
        if score >= RELATION_MIN_SCORE:
            result.append((pair[0], pair[1], score, {"talks": talks[pair], "links": links[pair], "people": people[pair]}))
    return sorted(result, key=lambda r: -r[2])


def person_graph(session: Session, now: dt.datetime, name: str) -> Optional[dict]:
    """The person in the middle, their topics around, tasks and promises under the topics, probable
    connections between the topics (D-058). None for an unknown person."""
    from briefing_service import briefing  # the briefing knows which tasks and talks belong to the person
    found = briefing(session, now, name)
    if found is None:
        return None
    names = {t.id: t.name for t in session.scalars(select(Topic))}
    topic_ids = [i for i in person_topics(session, found.name) if i in names]
    person_id = "person"
    nodes = [{"id": person_id, "label": found.name, "status": "person", "kind": "person",
              "url": f"/person?name={quote(found.name)}"}]
    edges = []
    for topic_id in topic_ids:
        nodes.append({"id": f"p{topic_id}", "label": names[topic_id], "status": "topic", "kind": "topic",
                      "url": f"/topics/{topic_id}"})
        edges.append({"source": person_id, "target": f"p{topic_id}", "type": "person"})
    task_ids = set()
    for row in found.mine + found.waiting:
        task_ids.add(row.id)
        nodes.append({"id": f"t{row.id}", "label": row.title, "status": row.status, "kind": row.kind,
                      "priority": row.priority, "url": f"/todos/{row.id}?person={quote(found.name)}"})
        anchor = f"p{row.topic_id}" if row.topic_id in topic_ids else person_id
        edges.append({"source": f"t{row.id}", "target": anchor, "type": "topic" if anchor != person_id else "person"})
    for link in session.scalars(select(TodoLink).where(TodoLink.low_id.in_(task_ids), TodoLink.high_id.in_(task_ids))):
        edges.append({"source": f"t{link.low_id}", "target": f"t{link.high_id}", "type": "link"})
    for a, b, score, why in topic_relations(session, topic_ids, ignore=found.name):
        edges.append({"source": f"p{a}", "target": f"p{b}", "type": "inferred", "score": score, "why": why})
    saved = {m.node_key: m for m in session.scalars(select(PersonMapNode).where(PersonMapNode.person == found.name.lower()))}
    for node in nodes:
        place = saved.get(node["id"])
        if place is not None:
            node.update(x=place.x, y=place.y, w=place.width, h=place.height)
    return {"nodes": nodes, "edges": edges, "truncated": False, "person": found.name}
