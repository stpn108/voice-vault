"""Findings of the security review (D-019): nothing is trashed at Plaud unless a complete, verified copy
was read in this very cycle, and odd answers from Plaud must neither be stored as content nor stop the cycle."""
import dataclasses
import datetime as dt
import gzip

import httpx
import pytest
from sqlalchemy import select

import import_service
from config import ConfigError, load_config
from database import Recording
from deletion_service import run_deletion
from import_service import ImportStats, run_import
from plaud_client import PlaudError
from sync_job import SyncJob
from tests.test_deletion_service import CFG, FakePlaud, NOW, make_recording
from tests.test_import_service import FakeClient, START_MS, raw_seg
from tests.test_plaud_client import detail_raw, make_client, ok

OLD_NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
# A day after the fake recordings were made: inside the time the import waits for speaker names.
FRESH = dt.datetime.fromtimestamp(START_MS / 1000, tz=dt.timezone.utc) + dt.timedelta(days=1)


def stored(session_factory):
    with session_factory() as s:
        return s.scalars(select(Recording)).all()


# --- empty or implausible content must not look like a finished import -------------------------------
@pytest.mark.parametrize("segments,summary", [
    ([raw_seg("A", "", 0, 900), raw_seg("B", "  ", 1000, 1900)], "## S"),  # renamed key: every text empty
    (None, "   \n"),  # whitespace summary
    ([{"speaker": "A", "text": "hello", "start_time": 0, "end_time": 900}], "## S"),  # content key is gone
])
def test_review_empty_text_is_not_imported_and_so_never_deleted(session_factory, segments, summary):
    client = FakeClient()
    client.add(segments=segments, summary=summary)
    stats = run_import(session_factory, client, lambda: OLD_NOW)
    assert stored(session_factory) == [] and stats.confirmed == set() and stats.skipped == 1


@pytest.mark.parametrize("start_ms", [0, -5, 1_000, 4_000_000_000_000])
def test_review_implausible_start_time_is_a_failure_not_an_old_recording(session_factory, monkeypatch, start_ms):
    client = FakeClient()
    client.add()
    monkeypatch.setattr(import_service, "from_epoch_ms", import_service.from_epoch_ms)
    original = client.get_detail

    def detail(plaud_id):
        d = original(plaud_id)
        d.start_ms = start_ms
        return d
    client.get_detail = detail
    client.list_recordings = lambda: [import_service.PlaudRecording("r1", "t", start_ms, 60_000)]
    stats = run_import(session_factory, client, lambda: OLD_NOW)
    assert stored(session_factory) == [] and stats.failed == 1


def test_review_a_transcript_that_ends_far_before_the_recording_is_stored_but_never_verified(session_factory):
    client = FakeClient()
    client.duration_ms = 600_000
    client.add(segments=[raw_seg("A", "short", 0, 30_000)])
    stats = run_import(session_factory, client, lambda: OLD_NOW)
    (rec,) = stored(session_factory)
    assert rec.verified_at is None and rec.verified_hash is None
    assert stats.confirmed == set() and stats.imported == 1


def test_review_text_with_nul_and_lone_surrogates_is_cleaned_and_imported(session_factory):
    client = FakeClient()
    client.add(segments=[raw_seg("A" * 400, "ab\x00c \ud800 d", 0, 900)], summary="## S\x00")
    stats = run_import(session_factory, client, lambda: OLD_NOW)
    (rec,) = stored(session_factory)
    assert stats.failed == 0 and rec.summary == "## S" and rec.verified_at is not None
    with session_factory() as s:
        from database import Segment
        seg = s.scalar(select(Segment))
        assert "\x00" not in seg.text and len(seg.speaker) == 255


def test_review_nan_and_huge_times_become_unknown(session_factory):
    client = FakeClient()
    client.add(segments=[raw_seg("A", "x", float("nan"), 10**15), raw_seg("A", "y", float("inf"), 5)])
    run_import(session_factory, client, lambda: OLD_NOW)
    with session_factory() as s:
        from database import Segment
        assert [(x.start_ms, x.end_ms) for x in s.scalars(select(Segment).order_by(Segment.idx))] == [(None, None), (None, 5)]


def test_review_one_unexpected_error_does_not_stop_the_other_recordings(session_factory):
    client = FakeClient()
    client.add("bad")
    client.add("good")
    client.fail_on["bad"] = EOFError("truncated gzip")
    stats = run_import(session_factory, client, lambda: OLD_NOW)
    assert stats.failed == 1 and stats.imported == 1 and stats.confirmed == {"good"}


def test_review_a_recording_restored_from_the_plaud_trash_is_not_deleted_for_good(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: OLD_NOW)
    with session_factory() as s:
        rec = s.scalar(select(Recording))
        rec.trashed_at = OLD_NOW - dt.timedelta(days=1)
        s.commit()
    run_import(session_factory, client, lambda: OLD_NOW)
    assert stored(session_factory)[0].trashed_at is None


# --- deletion follows what this cycle saw ----------------------------------------------------------------
def deletion(session_factory, imported, cfg=CFG, plaud=None, **recording):
    with session_factory() as s:
        s.add(make_recording(**recording))
        s.commit()
    plaud = plaud or FakePlaud()
    return run_deletion(session_factory, plaud, cfg, lambda: NOW, imported), plaud


def test_review_empty_plaud_list_trashes_nothing_although_the_database_says_yes(session_factory):
    stats, plaud = deletion(session_factory, ImportStats())
    assert plaud.trashed == [] and stats.trashed == 0 and stats.assumed_trashed == 0 and stats.held_back == 1


def test_review_only_recordings_verified_in_this_cycle_are_trashed(session_factory):
    imported = ImportStats(seen={"r1", "other"}, confirmed={"other"})
    stats, plaud = deletion(session_factory, imported)
    assert plaud.trashed == [] and stats.held_back == 1


def test_review_a_confirmed_recording_is_trashed(session_factory):
    imported = ImportStats(seen={"r1"}, confirmed={"r1"})
    stats, plaud = deletion(session_factory, imported)
    assert plaud.trashed == ["r1"] and stats.trashed == 1


def test_review_a_discarded_recording_still_listed_is_trashed_without_confirmation(session_factory):
    imported = ImportStats(seen={"r1"})
    stats, plaud = deletion(session_factory, imported, discarded_at=NOW - dt.timedelta(days=1))
    assert plaud.trashed == ["r1"]


def test_review_a_recording_missing_from_the_list_is_remembered_as_trashed_without_calling_plaud(session_factory):
    imported = ImportStats(seen={"another"}, confirmed={"another"})
    stats, plaud = deletion(session_factory, imported, plaud=FakePlaud(in_trash={"r1"}))
    assert plaud.trashed == [] and stats.assumed_trashed == 1
    assert stored(session_factory)[0].trashed_at is not None


def test_d042_a_recording_missing_from_the_list_but_not_in_the_trash_is_left_alone(session_factory):
    imported = ImportStats(seen={"another"}, confirmed={"another"})
    stats, plaud = deletion(session_factory, imported)
    assert plaud.trashed == [] and plaud.deleted == [] and stats.assumed_trashed == 0 and stats.held_back == 1
    assert stored(session_factory)[0].trashed_at is None


def test_d042_an_unreadable_trash_leaves_missing_recordings_alone(session_factory):
    class NoTrash(FakePlaud):
        def list_trash_ids(self):
            raise PlaudError("HTTP 500")
    imported = ImportStats(seen={"another"}, confirmed={"another"})
    stats, plaud = deletion(session_factory, imported, plaud=NoTrash())
    assert stats.assumed_trashed == 0 and stored(session_factory)[0].trashed_at is None


def test_d042_a_recording_marked_deleted_but_still_listed_is_taken_up_again(session_factory):
    client = FakeClient()
    client.add()
    run_import(session_factory, client, lambda: OLD_NOW)
    with session_factory() as s:
        rec = s.scalar(select(Recording))
        rec.trashed_at = rec.deleted_at = OLD_NOW - dt.timedelta(hours=1)
        s.commit()
    run_import(session_factory, client, lambda: OLD_NOW)
    rec = stored(session_factory)[0]
    assert rec.trashed_at is None and rec.deleted_at is None


def test_review_shadow_mode_changes_nothing_for_missing_recordings(session_factory):
    shadow = dataclasses.replace(CFG, delete_enabled=False)
    imported = ImportStats(seen={"another"}, confirmed={"another"})
    stats, plaud = deletion(session_factory, imported, cfg=shadow, plaud=FakePlaud(in_trash={"r1"}))
    assert stored(session_factory)[0].trashed_at is None and stats.shadow == 1


def test_review_the_number_of_trashings_per_cycle_is_capped(session_factory):
    capped = dataclasses.replace(CFG, max_trash_per_cycle=2)
    with session_factory() as s:
        for i in range(5):
            s.add(make_recording(plaud_id=f"r{i}"))
        s.commit()
    ids = {f"r{i}" for i in range(5)}
    plaud = FakePlaud()
    stats = run_deletion(session_factory, plaud, capped, lambda: NOW, ImportStats(seen=ids, confirmed=ids))
    assert len(plaud.trashed) == 2 and stats.held_back >= 1


def test_review_the_cycle_passes_its_own_import_result_to_the_deletion(session_factory, monkeypatch):
    seen = {}
    monkeypatch.setattr("sync_job.run_import", lambda *a, **k: ImportStats(seen={"x"}, confirmed={"x"}))
    monkeypatch.setattr("sync_job.run_deletion", lambda *a, **k: seen.setdefault("imported", a[4]))
    client = type("C", (), {"ensure_fresh_token": lambda s, n: None, "credential_seconds_left": lambda s, n: 10**9})()
    SyncJob(CFG, client, session_factory, notifier=lambda *a: True, now_fn=lambda: NOW).run()
    assert seen["imported"].confirmed == {"x"}


def test_review_repeated_failing_cycles_send_one_mail_a_day(session_factory, monkeypatch):
    mails = []
    monkeypatch.setattr("sync_job.run_import", lambda *a, **k: ImportStats(failed=1))
    monkeypatch.setattr("sync_job.run_deletion", lambda *a, **k: None)
    client = type("C", (), {"ensure_fresh_token": lambda s, n: None, "credential_seconds_left": lambda s, n: 10**9})()
    job = SyncJob(CFG, client, session_factory, notifier=lambda cfg, s, b: mails.append(s) or True, now_fn=lambda: NOW)
    for _ in range(5):
        job.run()
    assert mails == ["voice-vault: imports keep failing"]


def test_review_an_unexpected_exception_in_a_cycle_is_counted_not_raised(session_factory, monkeypatch):
    mails = []

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr("sync_job.run_import", boom)
    client = type("C", (), {"ensure_fresh_token": lambda s, n: None, "credential_seconds_left": lambda s, n: 10**9})()
    job = SyncJob(CFG, client, session_factory, notifier=lambda cfg, s, b: mails.append(s) or True, now_fn=lambda: NOW)
    for _ in range(3):
        job.run()
    assert len(mails) == 1


# --- what Plaud's download links may answer --------------------------------------------------------------
def client_for(handler):
    return make_client(handler)


def detail_with_links():
    return detail_raw()


@pytest.mark.parametrize("response", [
    httpx.Response(301, content=b"<Error><Code>PermanentRedirect</Code></Error>"),
    httpx.Response(204),
    httpx.Response(200, content=b"<?xml version='1.0'?><Error><Code>AccessDenied</Code></Error>"),
    httpx.Response(200, content=b"<html><body>denied</body></html>"),
    httpx.Response(200, content=b"\xff\xfe\x00bad utf8"),
    httpx.Response(200, content=b"\x1f\x8b\x08\x00truncated"),
])
def test_review_odd_download_answers_are_errors_not_content(response):
    client = client_for(lambda request: response)
    with pytest.raises(PlaudError):
        client._download("https://files.example.org/x")


def test_review_a_gzip_bomb_is_refused(monkeypatch):
    import plaud_client
    monkeypatch.setattr(plaud_client, "MAX_DOWNLOAD_BYTES", 1000)
    bomb = gzip.compress(b"a" * 100_000)
    client = client_for(lambda request: httpx.Response(200, content=bomb))
    with pytest.raises(PlaudError, match="larger"):
        client._download("https://files.example.org/x")


def test_review_an_oversized_download_is_refused(monkeypatch):
    import plaud_client
    monkeypatch.setattr(plaud_client, "MAX_DOWNLOAD_BYTES", 100)
    client = client_for(lambda request: httpx.Response(200, content=b"x" * 500))
    with pytest.raises(PlaudError, match="larger"):
        client._download("https://files.example.org/x")


@pytest.mark.parametrize("url", ["http://files.example.org/x", "https://127.0.0.1/x", "https://[::1]/x",
                                 "https://localhost/x", "https://db/x", "https://user@files.example.org/x"])
def test_review_download_links_to_internal_or_odd_hosts_are_refused(url):
    client = client_for(lambda request: httpx.Response(200, text="ok"))
    with pytest.raises(PlaudError):
        client._download(url)


def test_review_plain_text_and_json_downloads_still_work():
    assert client_for(lambda r: httpx.Response(200, text="## Summary\n- a"))._download("https://files.example.org/x") == "## Summary\n- a"
    assert client_for(lambda r: httpx.Response(200, json={"a": 1}))._download("https://files.example.org/x") == {"a": 1}
    assert client_for(lambda r: httpx.Response(200, content=gzip.compress(b'{"a": 2}')))._download("https://files.example.org/x") == {"a": 2}


@pytest.mark.parametrize("plaud_id", ["../x", "a/b", "a?b", "a b", "", "x" * 65, "a%2Fb"])
def test_review_ids_that_could_change_the_path_never_reach_plaud(plaud_id):
    calls = []
    client = client_for(lambda request: calls.append(request.url.path) or ok({}))
    for action in (lambda: client.get_detail(plaud_id), lambda: client.trash([plaud_id]),
                   lambda: client.delete_permanently([plaud_id])):
        with pytest.raises(PlaudError):
            action()
    assert calls == []


def test_review_listed_items_with_odd_shapes_are_skipped_not_fatal():
    items = [{"id": "ok1", "start_time": 1, "duration": 2}, {"id": "bad/../id"}, "not a dict",
             {"id": "ok2", "start_time": "soon"}, {"id": "ok3", "start_time": 5, "duration": 6}]
    recs = client_for(lambda request: ok(data_file_list=items)).list_recordings()
    assert [r.plaud_id for r in recs] == ["ok1", "ok3"]


def test_review_unreadable_detail_numbers_are_a_plaud_error():
    raw = detail_raw()
    raw["start_time"] = "yesterday"
    client = client_for(lambda request: ok(raw))
    with pytest.raises(PlaudError, match="unreadable"):
        client.get_detail("r1")


# --- configuration -------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,value", [
    ("MIN_AGE_MINUTES", "0"), ("MIN_AGE_MINUTES", "-5"), ("STABILITY_MINUTES", "0"),
    ("PERMANENT_DELETE_AFTER_HOURS", "-1"), ("IMPORT_INTERVAL_MINUTES", "0"), ("MAX_TRASH_PER_CYCLE", "0"),
    ("MIN_AGE_MINUTES", "seven"), ("MIN_SPEAKERS_NAMED_PERCENT", "101"), ("MIN_SPEAKERS_NAMED_PERCENT", "-1"),
])
def test_review_gate_settings_that_would_switch_a_gate_off_stop_the_start(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_config()


def test_review_an_empty_setting_means_the_default(monkeypatch):
    monkeypatch.setenv("MIN_AGE_MINUTES", "")
    assert load_config().min_age_minutes == 1 and load_config().max_trash_per_cycle == 20


@pytest.mark.parametrize("name,value", [("PERMANENT_DELETE_AFTER_HOURS", "0"), ("MIN_SPEAKERS_NAMED_PERCENT", "0"),
                                        ("MIN_SPEAKERS_NAMED_PERCENT", "100")])
def test_d022_the_ends_of_the_allowed_ranges_are_accepted(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    load_config()


# --- immediate deletion (D-022) and speaker names (D-023) -------------------------------------------------
def test_d022_with_the_defaults_a_confirmed_recording_is_trashed_and_deleted_in_one_cycle(session_factory):
    defaults = dataclasses.replace(load_config(), delete_enabled=True)
    assert (defaults.min_age_minutes, defaults.permanent_delete_after_hours) == (1, 0)
    imported = ImportStats(seen={"r1"}, confirmed={"r1"})
    with session_factory() as s:
        s.add(make_recording(ended_at=NOW - dt.timedelta(minutes=30)))
        s.commit()
    plaud = FakePlaud()
    stats = run_deletion(session_factory, plaud, defaults, lambda: NOW, imported)
    assert (plaud.trashed, plaud.deleted) == (["r1"], ["r1"]) and (stats.trashed, stats.deleted) == (1, 1)
    rec = stored(session_factory)[0]
    assert rec.trashed_at is not None and rec.deleted_at is not None


@pytest.mark.parametrize("overrides", [
    dict(is_plaud_processed=False), dict(verified_at=None, verified_hash=None), dict(duration_ms=0),
    dict(last_changed_at=NOW - dt.timedelta(minutes=3)),
])
def test_d022_immediate_deletion_still_needs_every_other_gate(session_factory, overrides):
    defaults = dataclasses.replace(load_config(), delete_enabled=True)
    imported = ImportStats(seen={"r1"}, confirmed={"r1"})
    with session_factory() as s:
        s.add(make_recording(ended_at=NOW - dt.timedelta(minutes=30), **overrides))
        s.commit()
    plaud = FakePlaud()
    run_deletion(session_factory, plaud, defaults, lambda: NOW, imported)
    assert plaud.trashed == [] and plaud.deleted == []


@pytest.mark.parametrize("labels,percent,expected", [
    (["Dennis", "Anna", "Markus Blinzinger", "Speaker 4"], 80, False),   # 3 of 4 = 75 %
    (["Dennis", "Anna", "Markus", "Jochen", "Speaker 5"], 80, True),     # 4 of 5 = 80 %
    (["Dennis", "Anna"], 80, True),
    (["Speaker 1", "Speaker 2"], 80, False),
    (["Sprecher 1", "Sprecherin 2", "Dennis"], 80, False),
    (["", "Dennis"], 80, False),
    (["Dennis"], 80, True),
    (["Speaker 1"], 80, False),
    (["Speaker 1", "Dennis"], 50, True),
    (["Speaker 1", "Speaker 2"], 0, True),                               # 0 switches the check off
    (["Speaker 1", "Dennis"], 100, False),
    (["SPEAKER 3", "dennis", "Unbekannt", "Redner 2"], 25, True),
])
def test_d023_the_share_of_named_speakers(labels, percent, expected):
    segments = [{"speaker": label, "start_ms": 0, "end_ms": 1, "text": "x"} for label in labels]
    assert import_service.speakers_named_enough(segments, percent) is expected


def named_client(speakers):
    client = FakeClient()
    client.add(segments=[raw_seg(s, f"text {i}", i * 1000, i * 1000 + 900) for i, s in enumerate(speakers)])
    return client


def test_d023_a_recording_with_too_few_named_speakers_is_not_imported_and_not_confirmed(session_factory):
    stats = run_import(session_factory, named_client(["Speaker 1", "Speaker 2", "Dennis"]), lambda: FRESH,
                       min_named_percent=80)
    assert stored(session_factory) == [] and stats.waiting_for_names == 1
    assert stats.confirmed == set() and stats.seen == {"r1"}


def test_d023_once_the_speakers_are_named_in_plaud_the_next_cycle_imports_it(session_factory):
    run_import(session_factory, named_client(["Speaker 1", "Dennis"]), lambda: FRESH, min_named_percent=80)
    assert stored(session_factory) == []
    stats = run_import(session_factory, named_client(["Anna", "Dennis"]), lambda: FRESH, min_named_percent=80)
    assert stats.imported == 1 and stats.confirmed == {"r1"} and stats.waiting_for_names == 0


def test_d023_a_stored_recording_whose_names_vanish_is_not_confirmed_for_deletion(session_factory):
    run_import(session_factory, named_client(["Anna", "Dennis"]), lambda: FRESH, min_named_percent=80)
    stats = run_import(session_factory, named_client(["Speaker 1", "Speaker 2"]), lambda: FRESH, min_named_percent=80)
    assert stats.confirmed == set() and stats.waiting_for_names == 1
    assert len(stored(session_factory)) == 1


def test_d023_the_cycle_passes_the_configured_share_to_the_import(session_factory, monkeypatch):
    seen = {}
    monkeypatch.setattr("sync_job.run_import", lambda *a, **k: seen.setdefault("args", a) and ImportStats())
    monkeypatch.setattr("sync_job.run_deletion", lambda *a, **k: None)
    client = type("C", (), {"ensure_fresh_token": lambda s, n: None, "credential_seconds_left": lambda s, n: 10**9})()
    cfg = dataclasses.replace(CFG, min_speakers_named_percent=70)
    SyncJob(cfg, client, session_factory, notifier=lambda *a: True, now_fn=lambda: NOW).run()
    assert seen["args"][3] == 70 and seen["args"][4] == 7


# --- at most seven days of waiting for names (D-024) -------------------------------------------------------
@pytest.mark.parametrize("days_after,imported", [(1, False), (6.9, False), (7.1, True), (30, True)])
def test_d024_without_enough_names_the_import_waits_at_most_the_configured_days(session_factory, days_after, imported):
    ended = dt.datetime.fromtimestamp(START_MS / 1000, tz=dt.timezone.utc) + dt.timedelta(minutes=1)
    now = ended + dt.timedelta(days=days_after)
    stats = run_import(session_factory, named_client(["Speaker 1", "Speaker 2"]), lambda: now,
                       min_named_percent=50, naming_wait_days=7)
    assert (len(stored(session_factory)) == 1) is imported
    assert (stats.confirmed == {"r1"}) is imported and (stats.waiting_for_names == 1) is not imported


@pytest.mark.parametrize("labels, enough", [
    (("Dennis", "Speaker 2"), False),             # a one-on-one with only the owner named waits (D-041)
    (("Dennis", "Jochen Rill"), True),
    (("Dennis", "Jochen Rill", "Speaker 3"), True),  # two of three: 67 percent
])
def test_d041_a_one_on_one_needs_both_names_by_default(labels, enough):
    segments = [{"speaker": s, "start_ms": 0, "end_ms": 1, "text": "x"} for s in labels]
    assert import_service.speakers_named_enough(segments, load_config().min_speakers_named_percent) is enough


def test_d024_a_wait_of_zero_days_imports_at_once(session_factory):
    stats = run_import(session_factory, named_client(["Speaker 1"]), lambda: FRESH, min_named_percent=50,
                       naming_wait_days=0)
    assert stats.imported == 1


def test_d024_the_log_names_the_unnamed_labels_but_never_a_real_name(session_factory, caplog):
    with caplog.at_level("INFO"):
        run_import(session_factory, named_client(["Speaker 2", "Speaker 3", "Dennis"]), lambda: FRESH,
                   min_named_percent=50)
    line = next(r.getMessage() for r in caplog.records if "Waiting for speaker names" in r.getMessage())
    assert "named=1 of 3" in line and "unnamed: Speaker 2, Speaker 3" in line and "Dennis" not in line
