"""Stored-recording queries and discard (REQ-002 criteria 1, 2, 4, 5, 6, 10)."""
import datetime as dt

import pytest
from sqlalchemy import select

import recording_service as svc
from database import Recording, Segment
from tests.helpers import add_recording

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
STABILITY = 10


def list_page(session, **kwargs):
    return svc.list_recordings(session, NOW, STABILITY, **kwargs)


def test_req_002_first_page_has_the_50_newest_and_a_cursor(db_session):
    for i in range(120):
        add_recording(db_session, i, segments=0)
    page = list_page(db_session)
    assert len(page.rows) == 50 and page.next_cursor
    assert [r.title for r in page.rows[:2]] == ["Title 119", "Title 118"]


def test_req_002_paging_has_no_skips_or_duplicates_even_if_a_new_recording_arrives(db_session):
    for i in range(120):
        add_recording(db_session, i, segments=0)
    first = list_page(db_session)
    add_recording(db_session, 500, segments=0)  # newest, arrives between the two requests
    second = list_page(db_session, cursor=first.next_cursor)
    third = list_page(db_session, cursor=second.next_cursor)
    seen = [r.title for r in first.rows + second.rows + third.rows]
    assert len(seen) == len(set(seen)) == 120
    assert "Title 500" not in seen
    assert third.next_cursor is None and len(third.rows) == 20


def test_paging_is_stable_for_recordings_with_the_same_start(db_session):
    same = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.timezone.utc)
    for i in range(60):
        add_recording(db_session, i, started=same, segments=0)
    first = list_page(db_session)
    second = list_page(db_session, cursor=first.next_cursor)
    titles = [r.title for r in first.rows + second.rows]
    assert len(titles) == len(set(titles)) == 60


@pytest.mark.parametrize("cursor", ["garbage", "bm90LWEtY3Vyc29y", "MjAyNi0xMC0wMVQwODowMDowMCswMDowMHx4"])
def test_invalid_cursor_raises_value_error(db_session, cursor):
    with pytest.raises(ValueError):
        list_page(db_session, cursor=cursor)


def test_cursor_roundtrip():
    started = dt.datetime(2026, 10, 1, 8, 0, 5, 123456, tzinfo=dt.timezone.utc)
    assert svc.decode_cursor(svc.encode_cursor(started, 42)) == (started, 42)


@pytest.mark.parametrize("overrides,expected", [
    (dict(deleted_at=NOW), "deleted"),
    (dict(trashed_at=NOW), "trashed"),
    (dict(is_plaud_processed=False), "waiting"),
    (dict(last_changed_at=NOW - dt.timedelta(minutes=5)), "waiting"),
    (dict(verified_at=None, verified_hash=None), "imported"),
    (dict(verified_hash="stale"), "imported"),
    ({}, "verified"),
])
def test_req_002_state_per_recording(db_session, overrides, expected):
    rec = add_recording(db_session, 0, **{"last_changed_at": NOW - dt.timedelta(hours=2), **overrides})
    assert svc.recording_state(rec, NOW, STABILITY) == expected


def test_deleted_wins_over_trashed():
    rec = Recording(deleted_at=NOW, trashed_at=NOW, last_changed_at=NOW, content_hash="h")
    assert svc.recording_state(rec, NOW, STABILITY) == "deleted"


def test_req_002_search_is_case_insensitive_over_transcript(db_session):
    rec = add_recording(db_session, 1, segments=0)
    db_session.add(Segment(recording_id=rec.id, idx=0, speaker="A", text="Die Kündigungsfrist beträgt drei Monate."))
    add_recording(db_session, 2, segments=1)
    db_session.commit()
    rows = list_page(db_session, query="kündigungsfrist").rows
    assert [r.title for r in rows] == ["Title 1"]


@pytest.mark.parametrize("query,expected", [
    ("title 3", ["Title 3"]),
    ("summary 4", ["Title 4"]),
    ("TEXT 5-1", ["Title 5"]),
])
def test_search_covers_title_summary_and_segments(db_session, query, expected):
    for i in range(6):
        add_recording(db_session, i)
    assert [r.title for r in list_page(db_session, query=query).rows] == expected


def test_empty_search_shows_everything(db_session):
    for i in range(3):
        add_recording(db_session, i)
    assert len(list_page(db_session, query="   ").rows) == 3


@pytest.mark.parametrize("query", ["100%", "a_b", "back\\slash"])
def test_search_treats_wildcards_literally(db_session, query):
    add_recording(db_session, 1, title="plain title")
    add_recording(db_session, 2, title=query)
    assert [r.title for r in list_page(db_session, query=query).rows] == [query]


def test_search_with_sql_metacharacters_is_harmless(db_session):
    add_recording(db_session, 1)
    assert list_page(db_session, query="'; DROP TABLE recordings; --").rows == []
    assert db_session.scalar(select(Recording).limit(1)) is not None


def test_req_002_discard_removes_content_and_keeps_a_tombstone(db_session):
    rec = add_recording(db_session, 1, segments=4)
    assert svc.discard(db_session, rec.id, NOW) is True
    db_session.expire_all()
    kept = db_session.get(Recording, rec.id)
    assert (kept.plaud_id, kept.title, kept.summary, kept.segment_count) == ("p1", "", "", 0)
    assert kept.discarded_at is not None
    assert db_session.query(Segment).count() == 0
    # Verification facts stay: the pipeline relies on them for a recording already trashed.
    assert kept.verified_hash == kept.content_hash and kept.verified_at is not None


def test_discarded_recording_disappears_from_list_search_and_detail(db_session):
    rec = add_recording(db_session, 1)
    svc.discard(db_session, rec.id, NOW)
    assert list_page(db_session).rows == []
    assert list_page(db_session, query="Title").rows == []
    assert svc.get_detail(db_session, rec.id) is None


def test_discarding_twice_or_a_missing_recording_returns_false(db_session):
    rec = add_recording(db_session, 1)
    assert svc.discard(db_session, rec.id, NOW) is True
    assert svc.discard(db_session, rec.id, NOW) is False
    assert svc.discard(db_session, 9999, NOW) is False


def test_req_002_discard_older_than_n_days_discards_exactly_those(db_session):
    for i in range(30):
        age_days = 100 if i < 12 else 10
        add_recording(db_session, i, started=NOW - dt.timedelta(days=age_days, minutes=i), segments=0)
    assert svc.count_older_than(db_session, 90, NOW) == 12
    assert svc.discard_older_than(db_session, 90, NOW) == 12
    assert len(list_page(db_session).rows) == 18
    assert svc.count_older_than(db_session, 90, NOW) == 0


def test_req_002_reading_does_not_change_rows(db_session):
    rec = add_recording(db_session, 1)
    before = (rec.summary, rec.segment_count, rec.discarded_at)
    list_page(db_session, query="Title")
    svc.get_detail(db_session, rec.id)
    svc.count_older_than(db_session, 1, NOW)
    db_session.expire_all()
    after = db_session.get(Recording, rec.id)
    assert (after.summary, after.segment_count, after.discarded_at) == before


# --- excerpt of a summary ---------------------------------------------------------------------
@pytest.mark.parametrize("summary,expected", [
    ("", ""),
    ("   \n\n  ", ""),
    ("plain text", "plain text"),
    ("## Heading\nBody text", "Heading \u00b7 Body text"),
    ("### Deep heading", "Deep heading"),
    ("> Date: today\n> Place: here", "Date: today \u00b7 Place: here"),
    ("- one\n- two\n* three\n+ four", "one \u00b7 two \u00b7 three \u00b7 four"),
    ("1. first\n2) second", "first \u00b7 second"),
    ("- [ ] open task\n- [x] done task\n- [X] other", "open task \u00b7 done task \u00b7 other"),
    ("**bold** and __also bold__ and `code`", "bold and also bold and code"),
    ("see [the docs](https://example.org/x) now", "see the docs now"),
    ("keep [Name] and [Other Name] as they are", "keep [Name] and [Other Name] as they are"),
    ("snake_case_name stays", "snake_case_name stays"),
    ("a *single* star stays", "a *single* star stays"),
    ("line one\n\n\n   line   two  ", "line one \u00b7 line two"),
    # Section numbers are dropped like list numbers: all leading marks are peeled off, for readability.
    ("## 1. Numbered heading", "Numbered heading"),
    ("> ## Quoted heading\n> - nested bullet", "Quoted heading \u00b7 nested bullet"),
])
def test_make_excerpt_strips_markdown_and_joins_lines(summary, expected):
    assert svc.make_excerpt(summary) == expected


def test_make_excerpt_of_a_realistic_summary_header():
    summary = ("> Datum: 06.10.2026 17:49:30\n> Ort: [Ort Einf\u00fcgen]\n> Teilnehmer: [Ian] [Dennis]\n\n"
               "## Sitzungsnotizen\n### Analyse\n- **Diskussion**: Ein Kunde hat Mails erhalten")
    out = svc.make_excerpt(summary)
    assert out == ("Datum: 06.10.2026 17:49:30 \u00b7 Ort: [Ort Einf\u00fcgen] \u00b7 Teilnehmer: [Ian] [Dennis] \u00b7 "
                   "Sitzungsnotizen \u00b7 Analyse \u00b7 Diskussion: Ein Kunde hat Mails erhalten")
    assert "#" not in out and ">" not in out and "**" not in out


def test_make_excerpt_cuts_at_a_word_and_marks_the_cut():
    out = svc.make_excerpt("alpha beta gamma delta epsilon", max_chars=14)
    assert out == "alpha beta\u2026"  # 'gamma' would be cut in half


def test_make_excerpt_keeps_a_word_that_ends_exactly_at_the_limit():
    assert svc.make_excerpt("alpha beta gamma", max_chars=10) == "alpha beta\u2026"
    assert svc.make_excerpt("alpha beta", max_chars=10) == "alpha beta"


def test_make_excerpt_hard_cuts_a_single_long_word():
    out = svc.make_excerpt("x" * 50, max_chars=10)
    assert out == "x" * 10 + "\u2026"


def test_make_excerpt_does_not_end_with_a_dangling_separator():
    out = svc.make_excerpt("one\ntwo\nthree\nfour", max_chars=9)
    assert not out.rstrip("\u2026").endswith((" ", "\u00b7", ",", ";", ":", "-"))


def test_make_excerpt_never_exceeds_the_limit_by_more_than_the_ellipsis():
    out = svc.make_excerpt("word " * 200)
    assert len(out) <= svc.EXCERPT_CHARS + 1 and out.endswith("\u2026")


def test_the_list_carries_the_cleaned_excerpt(db_session):
    add_recording(db_session, 1, summary="## Heading\n- **point** one\n- point two")
    assert list_page(db_session).rows[0].excerpt == "Heading \u00b7 point one \u00b7 point two"
