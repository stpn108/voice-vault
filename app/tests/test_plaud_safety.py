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
    stats, plaud = deletion(session_factory, imported)
    assert plaud.trashed == [] and stats.assumed_trashed == 1
    assert stored(session_factory)[0].trashed_at is not None


def test_review_shadow_mode_changes_nothing_for_missing_recordings(session_factory):
    shadow = dataclasses.replace(CFG, delete_enabled=False)
    imported = ImportStats(seen={"another"}, confirmed={"another"})
    stats, plaud = deletion(session_factory, imported, cfg=shadow)
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
    ("PERMANENT_DELETE_AFTER_HOURS", "0"), ("IMPORT_INTERVAL_MINUTES", "0"), ("MAX_TRASH_PER_CYCLE", "0"),
    ("MIN_AGE_MINUTES", "seven"),
])
def test_review_gate_settings_that_would_switch_a_gate_off_stop_the_start(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_config()


def test_review_an_empty_setting_means_the_default(monkeypatch):
    monkeypatch.setenv("MIN_AGE_MINUTES", "")
    assert load_config().min_age_minutes == 10080 and load_config().max_trash_per_cycle == 20
