"""Links between tasks and the graph of them (D-047).

Kept apart from todo_service, like topic_admin: taking a link off deletes a row, and only the web UI
imports this module. Every change is written to both tasks' history.
"""
import datetime as dt
import logging

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

import todo_service as todos
from database import Todo, TodoEvent, TodoLink, Topic

log = logging.getLogger(__name__)

GRAPH_NODES_MAX = 400  # beyond this a picture of the tasks stops being readable


class LinkError(ValueError):
    """A rejected request. The message is safe to show."""


def _pair(session: Session, a: int, b: int) -> tuple:
    if a == b:
        raise LinkError("a task cannot be linked to itself")
    first, second = session.get(Todo, a), session.get(Todo, b)
    if first is None or second is None:
        raise LinkError("no task with that id")
    return (first, second) if first.id < second.id else (second, first)


def _find(session: Session, low: Todo, high: Todo):
    return session.scalar(select(TodoLink).where(TodoLink.low_id == low.id, TodoLink.high_id == high.id))


def _log(session: Session, now: dt.datetime, kind: str, todo: Todo, other: Todo) -> None:
    session.add(TodoEvent(todo_id=todo.id, kind=kind, actor="owner", note=f"#{other.id} {other.title}",
                          before_json="{}", after_json="{}", created_at=now))


def link(session: Session, now: dt.datetime, a: int, b: int) -> bool:
    """Link two tasks. False if they were linked already."""
    low, high = _pair(session, a, b)
    if _find(session, low, high) is not None:
        return False
    session.add(TodoLink(low_id=low.id, high_id=high.id, created_at=now))
    _log(session, now, "linked", low, high)
    _log(session, now, "linked", high, low)
    session.commit()
    log.info("Tasks linked ids=%s,%s", low.id, high.id)
    return True


def unlink(session: Session, now: dt.datetime, a: int, b: int) -> bool:
    """Take a link off. False if there was none."""
    low, high = _pair(session, a, b)
    found = _find(session, low, high)
    if found is None:
        return False
    session.delete(found)
    _log(session, now, "unlinked", low, high)
    _log(session, now, "unlinked", high, low)
    session.commit()
    log.info("Tasks unlinked ids=%s,%s", low.id, high.id)
    return True


def linked_to(session: Session, todo_id: int) -> list:
    """The tasks linked to one task: (id, title, status, kind), open ones first."""
    rows = session.scalars(select(TodoLink).where(or_(TodoLink.low_id == todo_id, TodoLink.high_id == todo_id))).all()
    other_ids = [r.high_id if r.low_id == todo_id else r.low_id for r in rows]
    others = session.scalars(select(Todo).where(Todo.id.in_(other_ids))).all() if other_ids else []
    return sorted(((t.id, t.title, t.status, t.kind) for t in others),
                  key=lambda row: (row[2] not in todos.ACTIVE, row[1].lower()))


def link_map(session: Session, ids: list) -> dict:
    """For each of the given tasks: the linked tasks as (id, title, status), open ones first (D-048)."""
    pairs = session.scalars(select(TodoLink).where(or_(TodoLink.low_id.in_(ids), TodoLink.high_id.in_(ids)))).all() \
        if ids else []
    other_ids = {p.low_id for p in pairs} | {p.high_id for p in pairs}
    tasks = {t.id: t for t in session.scalars(select(Todo).where(Todo.id.in_(other_ids)))} if other_ids else {}
    result = {}
    for p in pairs:
        for mine, other in ((p.low_id, p.high_id), (p.high_id, p.low_id)):
            if mine in ids and other in tasks:
                t = tasks[other]
                result.setdefault(mine, []).append((t.id, t.title, t.status))
    for rows in result.values():
        rows.sort(key=lambda row: (row[2] not in todos.ACTIVE, row[1].lower()))
    return result


def graph(session: Session, *, done: bool = False, topics: bool = False, everything: bool = False,
          waiting: bool = False) -> dict:
    """Nodes and edges for the graph page. Tasks of excluded topics are left out, like in the list;
    what others owe the owner only on request (D-051)."""
    hidden = set(todos._excluded_ids(session))
    statuses = todos.STATUSES if done else todos.ACTIVE
    kinds = todos.KINDS if waiting else ("mine",)
    tasks = {t.id: t for t in session.scalars(select(Todo).where(Todo.status.in_(statuses), Todo.kind.in_(kinds)))
             if t.topic_id not in hidden}
    links = [(r.low_id, r.high_id) for r in session.scalars(select(TodoLink))
             if r.low_id in tasks and r.high_id in tasks]
    shown = set(tasks) if everything else {i for pair in links for i in pair}
    nodes, edges = [], []
    names = {t.id: t.name for t in session.scalars(select(Topic))}
    used_topics = set()
    for task_id in sorted(shown)[:GRAPH_NODES_MAX]:
        task = tasks[task_id]
        nodes.append({"id": f"t{task.id}", "label": task.title, "status": task.status, "kind": task.kind,
                      "priority": task.priority, "url": f"/todos/{task.id}"})
        if topics and task.topic_id and task.topic_id in names:
            used_topics.add(task.topic_id)
            edges.append({"source": f"t{task.id}", "target": f"p{task.topic_id}", "type": "topic"})
    kept = {n["id"] for n in nodes}
    for topic_id in sorted(used_topics):
        nodes.append({"id": f"p{topic_id}", "label": names[topic_id], "status": "topic", "kind": "topic",
                      "url": f"/topics/{topic_id}"})
    edges += [{"source": f"t{a}", "target": f"t{b}", "type": "link"} for a, b in links
              if f"t{a}" in kept and f"t{b}" in kept]
    return {"nodes": nodes, "edges": edges, "truncated": len(shown) > GRAPH_NODES_MAX}
