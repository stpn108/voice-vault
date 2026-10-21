"""Figures of the statistics page (D-035)."""
import datetime as dt

import stats_service as stats
import todo_service as svc
from tests.helpers import add_recording

NOW = dt.datetime(2026, 10, 21, 10, 0, tzinfo=dt.timezone.utc)   # Wednesday, KW 43
LAST_WEEK = NOW - dt.timedelta(days=7)


def test_d035_flow_counts_new_and_finished_tasks_per_week(db_session):
    a = svc.create_todo(db_session, LAST_WEEK, title="Alpha task here", actor="owner")
    svc.create_todo(db_session, NOW, title="Beta task there", actor="owner", priority=1)
    svc.update_todo(db_session, NOW, a.id, actor="owner", status="done")
    data = stats.figures(db_session, NOW)
    assert data["labels"][-1] == "KW 43" and len(data["labels"]) == stats.WEEKS
    assert data["flow"]["created"][-2:] == [1, 1] and data["flow"]["finished"][-2:] == [0, 1]
    assert data["tiles"]["open"] == 1 and data["tiles"]["net_4_weeks"] == 1


def test_d035_open_by_priority_replays_the_status_at_each_week_end(db_session):
    a = svc.create_todo(db_session, LAST_WEEK, title="Alpha task here", actor="owner", priority=2)
    svc.update_todo(db_session, NOW, a.id, actor="owner", status="done")
    data = stats.figures(db_session, NOW)
    assert data["open_by_priority"]["2"][-2:] == [1, 0]


def test_d035_undoing_a_completion_counts_the_task_as_open_again(db_session):
    a = svc.create_todo(db_session, LAST_WEEK, title="Alpha task here", actor="owner")
    svc.update_todo(db_session, NOW, a.id, actor="owner", status="done")
    event = svc.get_todo(db_session, NOW, a.id)[1][0]
    svc.undo_event(db_session, NOW + dt.timedelta(minutes=1), event.id)
    data = stats.figures(db_session, NOW + dt.timedelta(minutes=2))
    assert data["flow"]["finished"][-1] == 0 and data["open_by_priority"]["3"][-1] == 1


def test_d035_people_and_hours(db_session):
    svc.create_todo(db_session, NOW, title="Alpha task here", actor="owner", people=["Anna", "Jochen"])
    svc.create_todo(db_session, NOW, title="Beta task there", actor="owner", people=["Anna"])
    add_recording(db_session, 1, started=NOW - dt.timedelta(hours=2), duration_ms=5_400_000)
    data = stats.figures(db_session, NOW)
    assert data["people"] == {"names": ["Anna", "Jochen"], "open": [2, 1]}
    assert data["hours"][-1] == 1.5 and data["tiles"]["hours_week"] == 1.5


def test_d035_tasks_of_excluded_topics_are_left_out(db_session):
    svc.create_todo(db_session, NOW, title="Alpha task here", actor="owner", topic="Secret")
    svc.set_topic_excluded(db_session, "Secret", True)
    assert stats.figures(db_session, NOW)["tiles"]["open"] == 0
