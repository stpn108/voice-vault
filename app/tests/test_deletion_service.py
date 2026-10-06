"""Deletion (REQ-001 criteria 3, 4, 6): every condition must hold, doubt means no deletion."""
import dataclasses
import datetime as dt

import pytest
from sqlalchemy import select

from config import load_config
from database import Recording
from deletion_service import (
    may_delete_permanently, run_deletion, unmet_trash_conditions,
)
from plaud_client import PlaudAuthError, PlaudError

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
CFG = dataclasses.replace(load_config(), delete_enabled=True, min_age_minutes=15,
                          stability_minutes=10, permanent_delete_after_hours=24)


def make_recording(**overrides):
    base = dict(
        plaud_id="r1", title="t", started_at=NOW - dt.timedelta(hours=2),
        ended_at=NOW - dt.timedelta(hours=1), duration_ms=1, summary="s", segment_count=1,
        content_hash="h", is_plaud_processed=True, last_changed_at=NOW - dt.timedelta(hours=1),
        verified_hash="h", verified_at=NOW - dt.timedelta(minutes=50),
    )
    base.update(overrides)
    return Recording(**base)


class FakePlaud:
    def __init__(self, fail=None):
        self.trashed, self.deleted, self.fail = [], [], fail

    def trash(self, ids):
        if self.fail:
            raise self.fail
        self.trashed += ids

    def delete_permanently(self, ids):
        if self.fail:
            raise self.fail
        self.deleted += ids


def store(session_factory, **overrides):
    with session_factory() as s:
        s.add(make_recording(**overrides))
        s.commit()


def test_all_conditions_met_means_no_reasons():
    assert unmet_trash_conditions(make_recording(), NOW, CFG) == []


@pytest.mark.parametrize("overrides,reason", [
    (dict(is_plaud_processed=False), "plaud_not_processed"),
    (dict(ended_at=NOW - dt.timedelta(minutes=14)), "too_young"),
    (dict(verified_at=None, verified_hash=None), "import_not_verified"),
    (dict(verified_hash="old"), "import_not_verified"),
    (dict(last_changed_at=NOW - dt.timedelta(minutes=9)), "content_not_stable"),
    (dict(duration_ms=0), "duration_unknown"),
    (dict(duration_ms=-5), "duration_unknown"),
])
def test_req_001_each_unmet_condition_blocks_trashing(overrides, reason):
    assert reason in unmet_trash_conditions(make_recording(**overrides), NOW, CFG)


def test_zero_duration_recording_is_never_trashed_even_if_everything_else_holds(session_factory):
    # D-006: a recording whose length Plaud has not reported must not be deleted.
    store(session_factory, duration_ms=0, ended_at=NOW - dt.timedelta(days=30))
    plaud = FakePlaud()
    stats = run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.trashed == [] and stats.held_back == 1


def test_default_minimum_age_is_one_day():
    rec = make_recording(ended_at=NOW - dt.timedelta(hours=23))
    assert "too_young" in unmet_trash_conditions(rec, NOW, load_config())
    rec = make_recording(ended_at=NOW - dt.timedelta(hours=24))
    assert "too_young" not in unmet_trash_conditions(rec, NOW, load_config())


def test_age_boundary_is_inclusive_at_exactly_min_age():
    rec = make_recording(ended_at=NOW - dt.timedelta(minutes=15))
    assert "too_young" not in unmet_trash_conditions(rec, NOW, CFG)


def test_naive_datetimes_from_sqlite_are_treated_as_utc():
    rec = make_recording(ended_at=(NOW - dt.timedelta(hours=1)).replace(tzinfo=None),
                         last_changed_at=(NOW - dt.timedelta(hours=1)).replace(tzinfo=None))
    assert unmet_trash_conditions(rec, NOW, CFG) == []


@pytest.mark.parametrize("overrides", [
    dict(is_plaud_processed=False), dict(ended_at=NOW - dt.timedelta(minutes=1)),
    dict(verified_at=None), dict(last_changed_at=NOW),
])
def test_recording_with_unmet_condition_is_not_trashed(session_factory, overrides):
    store(session_factory, **overrides)
    plaud = FakePlaud()
    stats = run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.trashed == [] and stats.held_back == 1
    with session_factory() as s:
        assert s.scalar(select(Recording)).trashed_at is None


def test_eligible_recording_is_trashed_and_recorded(session_factory):
    store(session_factory)
    plaud = FakePlaud()
    stats = run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.trashed == ["r1"] and plaud.deleted == [] and stats.trashed == 1
    with session_factory() as s:
        assert s.scalar(select(Recording)).trashed_at is not None


def test_req_001_shadow_mode_only_logs(session_factory):
    store(session_factory)
    plaud = FakePlaud()
    cfg = dataclasses.replace(CFG, delete_enabled=False)
    stats = run_deletion(session_factory, plaud, cfg, lambda: NOW)
    assert plaud.trashed == [] and plaud.deleted == [] and stats.shadow == 1
    with session_factory() as s:
        assert s.scalar(select(Recording)).trashed_at is None


def test_permanent_delete_waits_for_configured_time(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(hours=23))
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.deleted == []


def test_req_001_permanent_delete_after_wait_for_verified_import(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(hours=24))
    plaud = FakePlaud()
    stats = run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.deleted == ["r1"] and stats.deleted == 1
    with session_factory() as s:
        assert s.scalar(select(Recording)).deleted_at is not None


def test_req_001_permanent_delete_never_without_verified_import(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(days=30), verified_hash="stale")
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.deleted == []


def test_already_deleted_recording_is_not_deleted_again(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(days=2), deleted_at=NOW - dt.timedelta(days=1))
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.deleted == []


def test_permanent_delete_in_shadow_mode_only_logs(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(days=2))
    plaud = FakePlaud()
    cfg = dataclasses.replace(CFG, delete_enabled=False)
    run_deletion(session_factory, plaud, cfg, lambda: NOW)
    assert plaud.deleted == []
    with session_factory() as s:
        assert s.scalar(select(Recording)).deleted_at is None


def test_failed_trash_is_not_recorded_and_retried_next_run(session_factory):
    store(session_factory)
    stats = run_deletion(session_factory, FakePlaud(fail=PlaudError("503")), CFG, lambda: NOW)
    assert stats.failed == 1
    with session_factory() as s:
        assert s.scalar(select(Recording)).trashed_at is None
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.trashed == ["r1"]


def test_auth_error_during_deletion_propagates(session_factory):
    store(session_factory)
    with pytest.raises(PlaudAuthError):
        run_deletion(session_factory, FakePlaud(fail=PlaudAuthError("401")), CFG, lambda: NOW)


def test_may_delete_permanently_requires_trashed_state():
    assert may_delete_permanently(make_recording(), NOW, CFG) is False


def test_req_002_discarded_recording_is_trashed_at_plaud_even_if_never_verified(session_factory):
    # D-009: the owner discarded it; verification and stability gates do not apply.
    store(session_factory, discarded_at=NOW - dt.timedelta(minutes=1), verified_at=None, verified_hash=None,
          last_changed_at=NOW, is_plaud_processed=False)
    plaud = FakePlaud()
    stats = run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.trashed == ["r1"] and stats.trashed == 1
    with session_factory() as s:
        assert s.scalar(select(Recording)).trashed_at is not None


@pytest.mark.parametrize("overrides,reason", [
    (dict(ended_at=NOW - dt.timedelta(minutes=14)), "too_young"),
    (dict(duration_ms=0), "duration_unknown"),
])
def test_req_002_discarded_recording_still_respects_age_and_duration(session_factory, overrides, reason):
    store(session_factory, discarded_at=NOW, **overrides)
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.trashed == []
    with session_factory() as s:
        rec = s.scalar(select(Recording))
    assert reason in unmet_trash_conditions(rec, NOW, CFG)


def test_discarded_recording_has_no_other_unmet_conditions():
    rec = make_recording(discarded_at=NOW, is_plaud_processed=False, verified_at=None, verified_hash=None,
                         last_changed_at=NOW)
    assert unmet_trash_conditions(rec, NOW, CFG) == []


def test_req_002_discarded_recording_is_only_logged_in_shadow_mode(session_factory):
    store(session_factory, discarded_at=NOW)
    plaud = FakePlaud()
    cfg = dataclasses.replace(CFG, delete_enabled=False)
    stats = run_deletion(session_factory, plaud, cfg, lambda: NOW)
    assert plaud.trashed == [] and stats.shadow == 1


def test_req_002_trashed_discarded_recording_is_deleted_permanently_after_the_wait_without_verification(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(hours=24), discarded_at=NOW - dt.timedelta(hours=25),
          verified_at=None, verified_hash=None)
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.deleted == ["r1"]


def test_discarded_recording_waits_for_the_permanent_delete_period(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(hours=23), discarded_at=NOW - dt.timedelta(hours=25))
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.deleted == []


def test_not_discarded_recording_still_needs_verification_for_the_permanent_delete(session_factory):
    store(session_factory, trashed_at=NOW - dt.timedelta(days=30), verified_hash="stale")
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, CFG, lambda: NOW)
    assert plaud.deleted == []
