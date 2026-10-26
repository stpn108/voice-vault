"""Owner care of people names and unnamed speakers (D-034)."""
import datetime as dt

import pytest
from sqlalchemy import select

import people_admin as admin
import todo_service as svc
from database import Segment, TodoEvent
from tests.helpers import add_recording

NOW = dt.datetime(2026, 10, 21, 9, 0, tzinfo=dt.timezone.utc)


def labels(session, recording_id):
    return [s.speaker for s in session.scalars(select(Segment).where(Segment.recording_id == recording_id)
                                               .order_by(Segment.idx))]


def name_speaker(session, recording, label, name):
    for segment in session.scalars(select(Segment).where(Segment.recording_id == recording.id,
                                                         Segment.speaker == label)):
        segment.speaker = name
    session.commit()


@pytest.fixture
def world(db_session):
    first = add_recording(db_session, 1, segments=4)   # Speaker 0, 1, 0, 1
    second = add_recording(db_session, 2, segments=2)
    name_speaker(db_session, first, "Speaker 0", "Jochen Rill")
    name_speaker(db_session, second, "Speaker 0", "Jochen Rill")
    task = svc.create_todo(db_session, NOW, title="Discuss budget", actor="owner", people=["Jochen Rill", "Anna"])
    return first, second, task


def test_d034_people_lists_names_from_tasks_and_transcripts_with_counts(db_session, world):
    rows = {r.name: (r.tasks, r.recordings, r.segments) for r in admin.people(db_session)}
    assert rows == {"Anna": (1, 0, 0), "Jochen Rill": (1, 2, 3)}


def test_d034_deleting_a_name_takes_it_off_tasks_and_leaves_unknown_speakers(db_session, world):
    first, second, task = world
    result = admin.delete_person(db_session, NOW, "jochen rill")
    assert result == {"name": "Jochen Rill", "tasks": 1, "segments": 3}
    assert svc.get_todo(db_session, NOW, task.id)[0].people == ["Anna"]
    assert labels(db_session, first.id) == ["Unbekannt 1", "Speaker 1", "Unbekannt 1", "Speaker 1"]
    event = db_session.scalars(select(TodoEvent).order_by(TodoEvent.id.desc())).first()
    assert event.kind == "updated" and "deleted" in event.note
    svc.undo_event(db_session, NOW, event.id)  # the task side can be undone
    assert svc.get_todo(db_session, NOW, task.id)[0].people == ["Anna", "Jochen Rill"]


def test_d034_a_second_deleted_name_gets_the_next_unknown_number(db_session, world):
    first, _, _ = world
    name_speaker(db_session, first, "Speaker 1", "Eve")
    admin.delete_person(db_session, NOW, "Jochen Rill")
    admin.delete_person(db_session, NOW, "Eve")
    assert labels(db_session, first.id) == ["Unbekannt 1", "Unbekannt 2", "Unbekannt 1", "Unbekannt 2"]


def test_d034_renaming_onto_an_existing_name_merges(db_session, world):
    first, _, task = world
    admin.rename_person(db_session, NOW, "Anna", "Jochen Rill")
    assert svc.get_todo(db_session, NOW, task.id)[0].people == ["Jochen Rill"]
    admin.rename_person(db_session, NOW, "Jochen Rill", "Jochen")
    assert labels(db_session, first.id)[0] == "Jochen"
    assert {r.name for r in admin.people(db_session)} == {"Jochen"}


def test_d034_orphans_show_unnamed_speakers_and_can_be_named(db_session, world):
    first, second, _ = world
    found = [(o.recording_id, o.label, o.segments) for o in admin.orphans(db_session)]
    assert found == [(second.id, "Speaker 1", 1), (first.id, "Speaker 1", 2)]
    assert admin.assign_speaker(db_session, first.id, "Speaker 1", "Anna") == 2
    assert labels(db_session, first.id) == ["Jochen Rill", "Anna", "Jochen Rill", "Anna"]


@pytest.mark.parametrize("label, name, message", [
    ("Speaker 1", "Speaker 7", "placeholder"),
    ("Speaker 1", "  ", "empty"),
    ("Jochen Rill", "Anna", "has a name already"),
    ("Speaker 9", "Anna", "no passages"),
])
def test_d034_assigning_is_checked(db_session, world, label, name, message):
    first, _, _ = world
    with pytest.raises(admin.PeopleError, match=message):
        admin.assign_speaker(db_session, first.id, label, name)


def test_d034_discarded_recordings_are_left_alone(db_session, world):
    first, _, _ = world
    gone = add_recording(db_session, 3, segments=2, discarded_at=NOW)
    name_speaker(db_session, gone, "Speaker 0", "Jochen Rill")
    admin.delete_person(db_session, NOW, "Jochen Rill")
    assert labels(db_session, gone.id)[0] == "Jochen Rill"
    assert gone.id not in [o.recording_id for o in admin.orphans(db_session)]
    with pytest.raises(admin.PeopleError, match="no recording"):
        admin.assign_speaker(db_session, gone.id, "Speaker 1", "Anna")



def test_d043_naming_a_speaker_also_fixes_the_summary_of_that_recording(db_session, world):
    first, second, _ = world
    first.summary = "**Teilnehmer:** [Dennis] [Speaker 1]\n- Speaker 1 übernimmt das Ticket. Speaker 10 nicht."
    second.summary = "Speaker 1 in einer anderen Aufnahme"
    db_session.commit()
    admin.assign_speaker(db_session, first.id, "Speaker 1", "Stephan Simmang")
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.summary == ("**Teilnehmer:** [Dennis] [Stephan Simmang]\n- Stephan Simmang übernimmt das Ticket. "
                             "Speaker 10 nicht.")
    assert second.summary == "Speaker 1 in einer anderen Aufnahme"



def test_d044_an_excluded_person_is_not_suggested_and_not_set_by_claude(db_session, world):
    first, _, task = world
    admin.set_person_excluded(db_session, "jochen rill", True)
    assert "Jochen Rill" not in svc.known_people(db_session)
    assert svc.speakers_of(db_session, first.id) == []
    made = svc.create_todo(db_session, NOW, title="Something else entirely", actor="claude", people=["Jochen Rill", "Anna"])
    assert svc.get_todo(db_session, NOW, made.id)[0].people == ["Anna"]
    # Claude cannot take off a name the owner set himself either
    svc.update_todo(db_session, NOW, task.id, actor="claude", people=["Anna"])
    assert svc.get_todo(db_session, NOW, task.id)[0].people == ["Anna", "Jochen Rill"]
    rows = {r.name: r.excluded for r in admin.people(db_session)}
    assert rows["Jochen Rill"] is True
    admin.set_person_excluded(db_session, "Jochen Rill", False)
    assert "Jochen Rill" in svc.known_people(db_session)


def test_d044_the_owner_may_still_set_an_excluded_name(db_session, world):
    admin.set_person_excluded(db_session, "Eve", True)
    made = svc.create_todo(db_session, NOW, title="Talk to Eve", actor="owner", people=["Eve"])
    assert svc.get_todo(db_session, NOW, made.id)[0].people == ["Eve"]


def test_d057_the_profile_follows_a_renamed_name_and_goes_with_a_deleted_one(db_session, world):
    import person_service as ps
    ps.update_profile(db_session, NOW, "Anna", actor="owner", role="CFO", current_work="Budget")
    ps.set_topic_note(db_session, NOW, "Anna", "Budget", "from Anna", actor="owner")
    ps.set_topic_note(db_session, NOW, "Anna", "SOC", "SOC from Anna", actor="owner")
    ps.update_profile(db_session, NOW, "Jochen Rill", actor="owner", role="CISO")
    ps.set_topic_note(db_session, NOW, "Jochen Rill", "Budget", "from Jochen", actor="owner")
    admin.rename_person(db_session, NOW, "Anna", "Jochen Rill")
    merged = ps.profile(db_session, "Jochen Rill")
    assert (merged.role, merged.current_work) == ("CISO", "Budget") and ps.profile(db_session, "Anna") is None
    assert sorted((t.name, n.note) for t, n in ps.clusters(db_session, "Jochen Rill")) == [
        ("Budget", "from Jochen"), ("SOC", "SOC from Anna")]
    import task_links
    from database import PersonMapNode
    task_links.save_layout(db_session, NOW, [{"key": "person", "x": 1, "y": 2}], person="Jochen Rill")
    from database import PersonTopicHidden, Topic
    admin.hide_topic(db_session, "Jochen Rill", db_session.scalar(select(Topic.id).where(Topic.name == "SOC")))
    admin.rename_person(db_session, NOW, "Jochen Rill", "Jochen")
    assert ps.profile(db_session, "Jochen").role == "CISO"
    assert [m.person for m in db_session.scalars(select(PersonMapNode))] == ["jochen"]
    assert [h.person for h in db_session.scalars(select(PersonTopicHidden))] == ["jochen"]
    assert len(ps.clusters(db_session, "Jochen")) == 1
    admin.delete_person(db_session, NOW, "Jochen")
    assert ps.profile(db_session, "Jochen") is None and ps.clusters(db_session, "Jochen") == []
    assert db_session.scalars(select(PersonMapNode)).all() == []
    assert db_session.scalars(select(PersonTopicHidden)).all() == []
