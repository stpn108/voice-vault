"""Living person profile, topic clusters and the person's own Mindmap (D-057, D-058)."""
import datetime as dt

import pytest
from sqlalchemy import select

import people_admin
import person_service as people
import task_links as links
import todo_service as svc
from database import PersonProfile, TextVersion, Topic
from tests.helpers import add_recording

NOW = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)


def make(session, title, **kw):
    return svc.create_todo(session, NOW, title=title, actor="owner", allow_similar=True, **kw).id


def test_d057_profile_fields_change_one_by_one_and_old_texts_are_kept(db_session):
    people.update_profile(db_session, NOW, "Jochen Rill", actor="claude", role="Head of SOC", current_work="SIEM")
    people.update_profile(db_session, NOW, "jochen rill", actor="owner", role="CISO")
    found = people.profile(db_session, "JOCHEN RILL")
    assert (found.role, found.current_work, found.updated_by) == ("CISO", "SIEM", "owner")
    assert [v.body for v in db_session.scalars(select(TextVersion))] == ["Head of SOC"]
    assert len(db_session.scalars(select(PersonProfile)).all()) == 1


def test_d057_topic_note_is_replaced_and_creates_the_profile(db_session):
    people.set_topic_note(db_session, NOW, "Anna", "Budget", "Draft by Friday", actor="claude")
    people.set_topic_note(db_session, NOW, "anna", "budget", "Approved", actor="claude")
    assert [(t.name, n.note) for t, n in people.clusters(db_session, "Anna")] == [("Budget", "Approved")]
    assert people.profile(db_session, "Anna") is not None


@pytest.mark.parametrize("action", [
    lambda s: people.update_profile(s, NOW, "Secret Person", actor="claude", role="x"),
    lambda s: people.set_topic_note(s, NOW, "Secret Person", "Budget", "x", actor="claude"),
])
def test_d057_excluded_people_get_no_profile(db_session, action):
    people_admin.set_person_excluded(db_session, "Secret Person", True)
    with pytest.raises(people.PersonError):
        action(db_session)
    assert people.profile(db_session, "Secret Person") is None


def test_d057_clusters_leave_out_excluded_topics(db_session):
    people.set_topic_note(db_session, NOW, "Anna", "Budget", "ok", actor="claude")
    people.set_topic_note(db_session, NOW, "Anna", "Secret", "hidden", actor="claude")
    svc.set_topic_excluded(db_session, "Secret", True)
    assert [t.name for t, _ in people.clusters(db_session, "Anna")] == ["Budget"]


def test_d058_relations_are_counted_from_talks_links_and_shared_people(db_session):
    rec = add_recording(db_session, 1)
    svc.add_topic_note(db_session, NOW, topic="Budget", recording_id=rec.id, note="a")
    svc.add_topic_note(db_session, NOW, topic="SOC", recording_id=rec.id, note="b")
    a = make(db_session, "Offer", topic="Budget", people=["Anna", "Ian"])
    b = make(db_session, "Staffing", topic="SOC", people=["Anna", "Ian"])
    make(db_session, "Party", topic="Lonely", people=["Anna"])
    links.link(db_session, NOW, a, b)
    ids = {t.name: t.id for t in db_session.scalars(select(Topic))}
    relations = people.topic_relations(db_session, list(ids.values()), ignore="Anna")
    assert relations == [(min(ids["Budget"], ids["SOC"]), max(ids["Budget"], ids["SOC"]), 5,
                          {"talks": 1, "links": 1, "people": 1})]


def test_d058_the_person_alone_does_not_make_topics_related(db_session):
    make(db_session, "Offer", topic="Budget", people=["Anna"])
    make(db_session, "Party", topic="Lonely", people=["Anna"])
    graph = people.person_graph(db_session, NOW, "Anna")
    assert not [e for e in graph["edges"] if e["type"] == "inferred"]


def test_d058_person_graph_has_the_person_topics_tasks_and_links(db_session):
    a = make(db_session, "Offer", topic="Budget", people=["Jochen Rill"])
    b = make(db_session, "Ticket", kind="waiting", people=["Jochen Rill"])
    links.link(db_session, NOW, a, b)
    graph = people.person_graph(db_session, NOW, "jochen rill")
    kinds = {n["id"]: n["kind"] for n in graph["nodes"]}
    assert kinds["person"] == "person" and kinds[f"t{a}"] == "mine" and kinds[f"t{b}"] == "waiting"
    assert {e["type"] for e in graph["edges"]} == {"person", "topic", "link"}
    assert f"/todos/{a}?person=Jochen%20Rill" in {n["url"] for n in graph["nodes"]}
    assert people.person_graph(db_session, NOW, "Nobody") is None


def test_d059_a_hidden_topic_leaves_profile_map_and_relations_and_claude_cannot_put_it_back(db_session):
    rec = add_recording(db_session, 1)
    svc.add_topic_note(db_session, NOW, topic="Budget", recording_id=rec.id, note="a")
    svc.add_topic_note(db_session, NOW, topic="SOC", recording_id=rec.id, note="b")
    make(db_session, "Offer", topic="Budget", people=["Anna", "Ian"])
    make(db_session, "Staffing", topic="SOC", people=["Anna", "Ian"])
    people.set_topic_note(db_session, NOW, "Anna", "Budget", "Draft", actor="claude")
    budget = db_session.scalar(select(Topic).where(Topic.name == "Budget"))
    people_admin.hide_topic(db_session, "anna", budget.id)
    assert people.clusters(db_session, "Anna") == [] and budget.id not in people.person_topics(db_session, "Anna")
    assert {n["label"] for n in people.person_graph(db_session, NOW, "Anna")["nodes"]} >= {"Offer"}
    assert "p%d" % budget.id not in {n["id"] for n in people.person_graph(db_session, NOW, "Anna")["nodes"]}
    with pytest.raises(people.PersonError, match="took topic"):
        people.set_topic_note(db_session, NOW, "Anna", "Budget", "again", actor="claude")
    ids = [t.id for t in db_session.scalars(select(Topic))]
    assert people.topic_relations(db_session, ids, ignore="Ian")[0][3]["people"] == 0  # Anna no longer counts
    assert people_admin.hidden_pairs(db_session)[0][0] == "Anna"
    assert people_admin.show_topic(db_session, "Anna", budget.id) is True
    assert [n.note for _, n in people.clusters(db_session, "Anna")] == ["Draft"]


@pytest.mark.parametrize("own, others, counts", [(1, 4, True), (1, 5, False)])
def test_d059_a_conversation_ties_a_person_to_its_topics_only_with_a_20_percent_share(db_session, own, others, counts):
    from database import Segment
    rec = add_recording(db_session, 1, segments=0)
    for i in range(own + others):
        db_session.add(Segment(recording_id=rec.id, idx=i, speaker="Anna" if i < own else "Bob", text="x"))
    db_session.commit()
    svc.add_topic_note(db_session, NOW, topic="Budget", recording_id=rec.id, note="a")
    assert bool(people.person_topics(db_session, "Anna")) is counts
