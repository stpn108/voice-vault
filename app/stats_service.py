"""Figures for the statistics page (D-035): task flow, open tasks by priority, open tasks per person,
conversation hours. Read only; tasks of excluded topics are left out, like in the task list.
"""
import datetime as dt
import json
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

import todo_service as todos
from database import Recording, Todo, TodoEvent
from utils import as_utc, day_bounds, local_day

WEEKS = 12
PEOPLE_MAX = 12
FINISHED = ("done", "dropped")


def _week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _weeks(now: dt.datetime) -> list:
    current = _week_start(local_day(now))
    return [current - dt.timedelta(weeks=WEEKS - 1 - i) for i in range(WEEKS)]


def _label(monday: dt.date) -> str:
    return f"KW {monday.isocalendar()[1]}"


def _timelines(session: Session, tasks: list) -> dict:
    """Per task: [(time, status)] in order, from every logged change that set a status (undo included)."""
    found = {t.id: [] for t in tasks}
    events = session.scalars(select(TodoEvent).where(TodoEvent.todo_id.in_(list(found)))
                             .order_by(TodoEvent.created_at, TodoEvent.id)).all() if found else []
    for event in events:
        status = json.loads(event.after_json).get("status")
        if status:
            found[event.todo_id].append((as_utc(event.created_at), status))
    for task in tasks:  # tasks from before the event log still have a start
        if not found[task.id]:
            found[task.id].append((as_utc(task.created_at), "open"))
    return found


def _status_at(timeline: list, moment: dt.datetime):
    status = None
    for when, value in timeline:
        if when > moment:
            break
        status = value
    return status


def figures(session: Session, now: dt.datetime) -> dict:
    hidden = todos._excluded_ids(session)
    stmt = select(Todo).where(Todo.kind == "mine")  # what others owe the owner is not his workload
    if hidden:
        stmt = stmt.where(Todo.topic_id.is_(None) | Todo.topic_id.not_in(hidden))
    tasks = session.scalars(stmt).all()
    timelines = _timelines(session, tasks)
    weeks = _weeks(now)
    labels = [_label(w) for w in weeks]
    index = {w: i for i, w in enumerate(weeks)}

    created, finished = [0] * WEEKS, [0] * WEEKS
    for task in tasks:
        week = _week_start(local_day(task.created_at))
        if week in index:
            created[index[week]] += 1
        timeline = timelines[task.id]
        if task.status in FINISHED and timeline and timeline[-1][1] in FINISHED:
            week = _week_start(local_day(timeline[-1][0]))
            if week in index:
                finished[index[week]] += 1

    open_by_priority = {p: [0] * WEEKS for p in todos.PRIORITIES}
    for i, monday in enumerate(weeks):
        end = min(day_bounds(monday + dt.timedelta(days=6))[1], now)
        for task in tasks:
            if as_utc(task.created_at) <= end and _status_at(timelines[task.id], end) in todos.ACTIVE:
                open_by_priority[task.priority][i] += 1

    people = Counter()
    for task in tasks:
        if task.status in todos.ACTIVE:
            people.update(n for n in (task.people or "").split("\n") if n)
    hidden = todos.excluded_people(session)
    top_people = sorted(((n, c) for n, c in people.items() if n.lower() not in hidden),
                        key=lambda pair: (-pair[1], pair[0].lower()))[:PEOPLE_MAX]

    hours = [0.0] * WEEKS
    first = day_bounds(weeks[0])[0]
    for started, duration in session.execute(select(Recording.started_at, Recording.duration_ms)
                                             .where(Recording.started_at >= first)):
        week = _week_start(local_day(started))
        if week in index:
            hours[index[week]] += (duration or 0) / 3_600_000
    hours = [round(h, 1) for h in hours]

    open_now = sum(1 for t in tasks if t.status in todos.ACTIVE)
    return {
        "labels": labels,
        "flow": {"created": created, "finished": finished},
        "open_by_priority": {str(p): values for p, values in open_by_priority.items()},
        "people": {"names": [n for n, _ in top_people], "open": [c for _, c in top_people]},
        "hours": hours,
        "tiles": {
            "open": open_now,
            "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
            "net_4_weeks": sum(created[-4:]) - sum(finished[-4:]),
            "hours_week": hours[-1],
        },
    }
