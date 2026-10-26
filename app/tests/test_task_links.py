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
    assert links.link_counts(db_session, [a, b]) == {a: 1, b: 1}
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
