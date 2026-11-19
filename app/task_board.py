"""The task board (REQ-008, D-074): the owner's unfinished tasks and the ones finished lately,
with the status as columns and the priority or the topic as rows.

Read only. The board is built from `todo_service.list_todos`, so it hides what the list hides
(excluded topics) and changes nothing.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

import todo_service as todos

COLUMNS = ("open", "in_progress", "done")
GROUPS = ("priority", "topic", "none")
FILTERS = ("overdue", "soon", "new")
DONE_DAYS = 7  # a finished task stays in the "done" column this long
SOON_DAYS = 7  # "due this week": due today or within this many days
NEW_DAYS = 2   # a task is "new" this long after it was written down


class BoardError(ValueError):
    """A rejected request. The message is safe to show to the caller."""


@dataclass
class Lane:
    key: str  # "p<priority>", "t<topic id>", "t0" (no topic) or "all"; stable, the browser remembers folded rows by it
    label: str  # the topic name; empty where the UI names the row itself
    priority: Optional[int]
    cells: dict = field(default_factory=lambda: {status: [] for status in COLUMNS})


@dataclass
class Board:
    lanes: list
    counts: dict  # column -> cards shown
    filter_counts: dict  # filter -> unfinished tasks it matches, whatever filter is on
    truncated: bool

    @property
    def rows(self) -> list:
        return [row for lane in self.lanes for status in COLUMNS for row in lane.cells[status]]


def is_new(row, now: dt.datetime) -> bool:
    return row.status in todos.ACTIVE and now - row.created_at < dt.timedelta(days=NEW_DAYS)


def matches(row, name: str, now: dt.datetime, today: dt.date) -> bool:
    """Whether an unfinished task passes a quick filter."""
    if row.status not in todos.ACTIVE:
        return False
    if name == "new":
        return is_new(row, now)
    if row.due_date is None:
        return False
    if name == "overdue":
        return row.due_date < today
    return today <= row.due_date <= today + dt.timedelta(days=SOON_DAYS)


def _lanes(rows: list, group: str) -> list:
    if group == "priority":
        return [Lane(f"p{p}", "", p) for p in todos.PRIORITIES]
    if group == "none":
        return [Lane("all", "", None)]
    named = sorted({(row.topic_id, row.topic) for row in rows if row.topic_id}, key=lambda pair: pair[1].lower())
    lanes = [Lane(f"t{topic_id}", name, None) for topic_id, name in named]
    if any(row.topic_id is None for row in rows):
        lanes.append(Lane("t0", "", None))
    return lanes


def board(session: Session, now: dt.datetime, today: dt.date, *, group: str = "priority", only: str = "",
          topic: Optional[str] = None, person: Optional[str] = None) -> Board:
    """The owner's own tasks as a board. `only` is one of FILTERS or empty."""
    if group not in GROUPS:
        raise BoardError(f"group must be one of: {', '.join(GROUPS)}")
    if only and only not in FILTERS:
        raise BoardError(f"only must be one of: {', '.join(FILTERS)}")
    active, truncated = todos.list_todos(session, now, status="open", topic=topic, person=person,
                                         limit=todos.LIST_LIMIT_MAX, sort="priority")
    filter_counts = {name: sum(matches(row, name, now, today) for row in active) for name in FILTERS}
    if only:
        rows = [row for row in active if matches(row, only, now, today)]
    else:
        done = todos.list_todos(session, now, status="done", topic=topic, person=person, limit=todos.LIST_LIMIT_MAX,
                                sort="priority", done_since=now - dt.timedelta(days=DONE_DAYS))[0]
        rows = active + sorted(done, key=lambda row: row.done_at, reverse=True)
    lanes = _lanes(rows, group)
    by_key = {lane.key: lane for lane in lanes}
    for row in rows:
        key = f"p{row.priority}" if group == "priority" else "all" if group == "none" else f"t{row.topic_id or 0}"
        by_key[key].cells[row.status].append(row)
    counts = {status: sum(len(lane.cells[status]) for lane in lanes) for status in COLUMNS}
    return Board(lanes, counts, filter_counts, truncated)
