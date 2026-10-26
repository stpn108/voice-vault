"""Links between tasks and the graph (D-047)."""
import datetime as dt

import pytest
from sqlalchemy import select

import task_links as links
import todo_service as svc
from database import TodoEvent

NOW = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)


def make(session, title, **kw):
    return svc.create_todo(session, NOW, title=title, actor="owner", allow_similar=True, **kw).id


def test_d047_link_is_stored_once_logged_on_both_and_can_be_removed(db_session):
    a, b = make(db_session, "Alpha"), make(db_session, "Beta")
    assert links.link(db_session, NOW, b, a) is True
    assert links.link(db_session, NOW, a, b) is False
    assert [row[1] for row in links.linked_to(db_session, a)] == ["Beta"]
    assert links.link_map(db_session, [a, b]) == {a: [(b, "Beta", "open")], b: [(a, "Alpha", "open")]}
    kinds = sorted(e.kind for e in db_session.scalars(select(TodoEvent).where(TodoEvent.kind == "linked")))
    assert kinds == ["linked", "linked"]
    assert links.unlink(db_session, NOW, a, b) is True and links.linked_to(db_session, a) == []
    assert links.unlink(db_session, NOW, a, b) is False


@pytest.mark.parametrize("pair, message", [((1, 1), "itself"), ((1, 999), "no task")])
def test_d047_bad_links_are_refused(db_session, pair, message):
    make(db_session, "Alpha")
    with pytest.raises(links.LinkError, match=message):
        links.link(db_session, NOW, *pair)


def test_d047_graph_shows_linked_tasks_and_hides_finished_unless_asked(db_session):
    a, b, c = make(db_session, "Alpha", topic="Budget"), make(db_session, "Beta"), make(db_session, "Gamma")
    make(db_session, "Lonely")
    links.link(db_session, NOW, a, b)
    links.link(db_session, NOW, b, c)
    svc.update_todo(db_session, NOW, c, actor="owner", status="done")
    plain = links.graph(db_session)
    assert {n["label"] for n in plain["nodes"]} == {"Alpha", "Beta"} and len(plain["edges"]) == 1
    full = links.graph(db_session, done=True, topics=True, everything=True)
    labels = {n["label"] for n in full["nodes"]}
    assert labels == {"Alpha", "Beta", "Gamma", "Lonely", "Budget"}
    assert {e["type"] for e in full["edges"]} == {"link", "topic"}


def test_d047_graph_leaves_out_tasks_of_excluded_topics(db_session):
    a, b = make(db_session, "Alpha", topic="Secret"), make(db_session, "Beta")
    links.link(db_session, NOW, a, b)
    svc.set_topic_excluded(db_session, "Secret", True)
    assert links.graph(db_session)["nodes"] == []



def test_d051_waiting_entries_are_left_out_of_the_graph_unless_asked(db_session):
    a = make(db_session, "Alpha")
    b = make(db_session, "Concept from Jochen", kind="waiting", people=["Jochen"])
    links.link(db_session, NOW, a, b)
    assert links.graph(db_session)["nodes"] == []
    assert {n["label"] for n in links.graph(db_session, waiting=True)["nodes"]} == {"Alpha", "Concept from Jochen"}


# --- remembered positions and sizes (D-054) -----------------------------------------------------------
def test_d054_positions_and_sizes_are_stored_and_come_back_with_the_graph(db_session):
    a, b = make(db_session, "Alpha"), make(db_session, "Beta")
    links.link(db_session, NOW, a, b)
    assert links.save_layout(db_session, NOW, [{"key": f"t{a}", "x": 10.5, "y": -20, "w": 300, "h": 90},
                                              {"key": f"t{b}", "x": 200, "y": 40}]) == 2
    nodes = {n["id"]: n for n in links.graph(db_session)["nodes"]}
    assert (nodes[f"t{a}"]["x"], nodes[f"t{a}"]["y"], nodes[f"t{a}"]["w"], nodes[f"t{a}"]["h"]) == (10.5, -20, 300, 90)
    assert nodes[f"t{b}"]["w"] is None
    # moving keeps the size unless a size is sent
    links.save_layout(db_session, NOW, [{"key": f"t{a}", "x": 0, "y": 0}])
    assert {n["id"]: n for n in links.graph(db_session)["nodes"]}[f"t{a}"]["w"] == 300
    links.save_layout(db_session, NOW, [{"key": f"t{a}", "x": 0, "y": 0, "w": None, "h": None}])
    assert {n["id"]: n for n in links.graph(db_session)["nodes"]}[f"t{a}"]["w"] is None


@pytest.mark.parametrize("entry", [
    {"key": "x1", "x": 0, "y": 0}, {"key": "t1", "x": float("nan"), "y": 0}, {"key": "t1", "x": 2e6, "y": 0},
    {"key": "t1", "x": 0, "y": 0, "w": 5}, {"key": "t1", "y": 0}, {"key": "t1", "x": True, "y": 0}, "junk",
])
def test_d054_bad_layout_entries_are_refused(db_session, entry):
    with pytest.raises(links.LinkError):
        links.save_layout(db_session, NOW, [entry])
