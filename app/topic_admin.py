"""Owner-only deletion of topics (REQ-006, D-017).

Kept apart from todo_service on purpose: that module has no way to delete anything and is the only
one the MCP tools use. Only the web UI imports this module, behind a confirmation page.
"""
import datetime as dt
import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import todo_service
from database import Todo, Topic, TopicNote

log = logging.getLogger(__name__)


def topic_counts(session: Session, topic_id: int) -> Optional[tuple]:
    """(topic, open tasks, all tasks, notes) or None for an unknown topic."""
    topic = session.get(Topic, topic_id)
    if topic is None:
        return None
    tasks = session.scalars(select(Todo).where(Todo.topic_id == topic.id)).all()
    notes = session.scalar(select(func.count()).select_from(TopicNote).where(TopicNote.topic_id == topic.id))
    return topic, sum(1 for t in tasks if t.status == "open"), len(tasks), notes


def delete_topic(session: Session, now: dt.datetime, topic_id: int, *, keep_excluded: bool = False) -> Optional[dict]:
    """Delete a topic and its notes. Its tasks stay, without a topic, each change logged and undoable.

    With `keep_excluded` the topic row stays as an excluded topic, so the routine does not
    create it again from the next conversation. Returns what was removed, or None if unknown.
    """
    found = topic_counts(session, topic_id)
    if found is None:
        return None
    topic, _, tasks, notes = found
    topic_name = topic.name
    todo_service.detach_topic_tasks(session, now, topic, "owner", "topic deleted")
    for note in session.scalars(select(TopicNote).where(TopicNote.topic_id == topic.id)).all():
        session.delete(note)
    if keep_excluded:
        topic.excluded = True
    else:
        session.delete(topic)
    session.commit()
    log.info("Topic deleted id=%s tasks_detached=%s notes_removed=%s kept_excluded=%s",
             topic.id, tasks, notes, keep_excluded)
    return {"name": topic_name, "tasks": tasks, "notes": notes, "excluded": keep_excluded}
