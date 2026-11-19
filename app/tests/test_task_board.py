"""The task board (REQ-008, D-074): the service that sorts tasks into columns and rows."""
import datetime as dt

import pytest

import task_board as tb
import todo_service as svc

NOW = dt.datetime(2026, 11, 19, 9, 0, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 11, 19)


def make(session, title, at=NOW, status=None, **kw):
    todo = svc.create_todo(session, at, title=title, actor="owner", allow_similar=True, **kw)
    if status:
        svc.update_todo(session, at, todo.id, actor="owner", status=status)
    return todo.id


def titles(cell):
    return [row.title for row in cell]


def test_req_008_columns_are_the_status_and_done_only_of_the_last_seven_days(db_session):
    make(db_session, "Open one")
    make(db_session, "Started one", status="in_progress")
    make(db_session, "Done yesterday", at=NOW - dt.timedelta(days=1), status="done")
    make(db_session, "Done today", status="done")
    make(db_session, "Done long ago", at=NOW - dt.timedelta(days=8), status="done")
    make(db_session, "Dropped", status="dropped")
    make(db_session, "Promised", kind="waiting", people=["Anna"])
    board = tb.board(db_session, NOW, TODAY, group="none")
    cells = board.lanes[0].cells
    assert titles(cells["open"]) == ["Open one"] and titles(cells["in_progress"]) == ["Started one"]
    assert titles(cells["done"]) == ["Done today", "Done yesterday"]  # the latest first
    assert board.counts == {"open": 1, "in_progress": 1, "done": 2}
    assert len(board.rows) == 4


def test_req_008_rows_are_the_four_priorities_with_due_dates_first(db_session):
    make(db_session, "Urgent open", priority=1)
    make(db_session, "Urgent started", priority=1, status="in_progress")
    make(db_session, "Normal without date", priority=3)
    make(db_session, "Normal due later", priority=3, due="2026-12-01")
    make(db_session, "Normal due soon", priority=3, due="2026-11-20")
    board = tb.board(db_session, NOW, TODAY)
    assert [(lane.key, lane.priority) for lane in board.lanes] == [("p1", 1), ("p2", 2), ("p3", 3), ("p4", 4)]
    urgent, high, normal, low = board.lanes
    assert titles(urgent.cells["open"]) == ["Urgent open"] and titles(urgent.cells["in_progress"]) == ["Urgent started"]
    assert titles(normal.cells["open"]) == ["Normal due soon", "Normal due later", "Normal without date"]
    assert not any(high.cells.values()) and not any(low.cells.values())  # empty rows stay, as places to drop a card


def test_req_008_rows_by_topic_are_alphabetical_with_no_topic_last(db_session):
    make(db_session, "Book hotel", topic="Messe Frankfurt")
    make(db_session, "Check numbers", topic="budget 2027")
    make(db_session, "Call back")
    board = tb.board(db_session, NOW, TODAY, group="topic")
    assert [lane.label for lane in board.lanes] == ["budget 2027", "Messe Frankfurt", ""]
    assert board.lanes[-1].key == "t0" and titles(board.lanes[-1].cells["open"]) == ["Call back"]
    assert all(lane.priority is None for lane in board.lanes)


@pytest.mark.parametrize("only, expected", [
    ("overdue", ["Late"]),
    ("soon", ["Today", "In a week"]),
    ("new", ["Fresh"]),
])
def test_req_008_quick_filters_narrow_the_board_and_keep_their_counts(db_session, only, expected):
    old = NOW - dt.timedelta(days=10)
    make(db_session, "Late", at=old, due="2026-11-18")
    make(db_session, "Today", at=old, due="2026-11-19")
    make(db_session, "In a week", at=old, due="2026-11-26")
    make(db_session, "In eight days", at=old, due="2026-11-27")
    make(db_session, "Fresh", at=NOW - dt.timedelta(hours=47))
    make(db_session, "Two days old", at=NOW - dt.timedelta(hours=48))
    make(db_session, "Late but done", at=old, due="2026-11-01", status="done")
    board = tb.board(db_session, NOW, TODAY, group="none", only=only)
    assert titles(board.rows) == expected
    assert board.filter_counts == {"overdue": 1, "soon": 2, "new": 1}
    assert board.counts["done"] == 0  # a filter is about unfinished tasks


def test_req_008_topic_and_person_narrow_the_board_and_excluded_topics_stay_out(db_session):
    make(db_session, "Budget with Anna", topic="Budget", people=["Anna"])
    make(db_session, "Budget alone", topic="Budget")
    make(db_session, "Family trip", topic="Family")
    svc.set_topic_excluded(db_session, "Family", True)
    assert titles(tb.board(db_session, NOW, TODAY).rows) == ["Budget with Anna", "Budget alone"]
    assert titles(tb.board(db_session, NOW, TODAY, person="anna").rows) == ["Budget with Anna"]
    assert titles(tb.board(db_session, NOW, TODAY, topic="Nothing").rows) == []


@pytest.mark.parametrize("kwargs", [{"group": "colour"}, {"only": "nonsense"}])
def test_req_008_unknown_grouping_or_filter_is_rejected(db_session, kwargs):
    with pytest.raises(tb.BoardError):
        tb.board(db_session, NOW, TODAY, **kwargs)


def test_req_010_rows_by_person_show_a_task_under_each_of_its_people_and_count_it_once(db_session):
    import people_admin
    make(db_session, "With both", people=["Jochen Rill", "anna"], status="in_progress")
    make(db_session, "With Anna", people=["Anna"])
    make(db_session, "Alone")
    make(db_session, "With the excluded", people=["Secret Person"])
    people_admin.set_person_excluded(db_session, "Secret Person", True)
    board = tb.board(db_session, NOW, TODAY, group="person")
    assert [(lane.key, lane.label) for lane in board.lanes] == [("n:anna", "Anna"), ("n:jochen rill", "Jochen Rill"), ("n0", "")]
    anna, jochen, nobody = board.lanes
    assert titles(anna.cells["open"]) == ["With Anna"] and titles(anna.cells["in_progress"]) == ["With both"]
    assert titles(jochen.cells["in_progress"]) == ["With both"] and not jochen.cells["open"]
    assert titles(nobody.cells["open"]) == ["Alone", "With the excluded"]
    assert board.counts == {"open": 3, "in_progress": 1, "done": 0} and len(board.rows) == 4
