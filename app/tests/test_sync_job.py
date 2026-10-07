"""Cycle behaviour (REQ-001 criteria 7, 8): token handling and abort on auth failure."""
import dataclasses
import datetime as dt

import pytest

import main
from config import load_config
from import_service import ImportStats
from plaud_client import PlaudAuthError, PlaudError
from sync_job import SyncJob

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
CFG = dataclasses.replace(load_config(), token_warn_days=5)


class FakeClient:
    def __init__(self, seconds_left=100 * 86400, refresh_error=None):
        self.seconds_left = seconds_left
        self.refresh_error = refresh_error
        self.refresh_checks = 0

    def ensure_fresh_token(self, now_epoch):
        self.refresh_checks += 1
        if self.refresh_error:
            raise self.refresh_error

    def credential_seconds_left(self, now_epoch):
        return self.seconds_left


def make_job(client, notifier=None, session_factory=None):
    mails = []
    job = SyncJob(CFG, client, session_factory or (lambda: None),
                  notifier=notifier or (lambda cfg, s, b: mails.append(s) or True),
                  now_fn=lambda: NOW)
    return job, mails


@pytest.fixture
def stubs(monkeypatch):
    calls = []
    monkeypatch.setattr("sync_job.run_import", lambda *a, **k: calls.append("import") or ImportStats())
    monkeypatch.setattr("sync_job.run_deletion", lambda *a, **k: calls.append("delete"))
    return calls


def test_healthy_token_runs_import_then_deletion(stubs):
    job, mails = make_job(FakeClient())
    job.run()
    assert stubs == ["import", "delete"] and mails == []


def test_req_001_token_with_less_than_5_days_warns_and_mails_once_per_day(stubs, caplog):
    job, mails = make_job(FakeClient(seconds_left=4 * 86400))
    with caplog.at_level("WARNING"):
        job.run()
        job.run()
    assert stubs == ["import", "delete", "import", "delete"]
    assert "expires soon" in caplog.text
    assert len(mails) == 1


def test_token_with_exactly_5_days_does_not_warn(stubs):
    job, mails = make_job(FakeClient(seconds_left=5 * 86400))
    job.run()
    assert mails == []


def test_expired_token_aborts_without_import_or_deletion(stubs):
    job, mails = make_job(FakeClient(seconds_left=-10))
    job.run()
    assert stubs == [] and len(mails) == 1


def test_unreadable_token_expiry_still_runs(stubs, caplog):
    job, _ = make_job(FakeClient(seconds_left=None))
    with caplog.at_level("WARNING"):
        job.run()
    assert stubs == ["import", "delete"] and "cannot be read" in caplog.text


def test_req_001_auth_error_during_import_skips_deletion(monkeypatch):
    calls = []

    def boom(*a, **k):
        raise PlaudAuthError("401")

    monkeypatch.setattr("sync_job.run_import", boom)
    monkeypatch.setattr("sync_job.run_deletion", lambda *a, **k: calls.append("delete"))
    job, mails = make_job(FakeClient())
    job.run()
    assert calls == [] and len(mails) == 1


def test_plaud_error_during_import_skips_deletion(monkeypatch):
    calls = []

    def boom(*a, **k):
        raise PlaudError("list failed")

    monkeypatch.setattr("sync_job.run_import", boom)
    monkeypatch.setattr("sync_job.run_deletion", lambda *a, **k: calls.append("delete"))
    job, _ = make_job(FakeClient())
    job.run()
    assert calls == []


def test_req_001_scheduler_does_not_overlap_runs():
    job, _ = make_job(FakeClient())
    scheduler = main.build_scheduler(job, 10)
    sched_job = scheduler.get_job("plaud_sync")
    assert sched_job.max_instances == 1 and sched_job.coalesce is True
    assert sched_job.trigger.interval == dt.timedelta(minutes=10)


def test_req_004_cycle_renews_token_before_importing(stubs):
    client = FakeClient()
    job, _ = make_job(client)
    job.run()
    assert client.refresh_checks == 1 and stubs == ["import", "delete"]


def test_req_004_rejected_refresh_token_aborts_cycle_and_mails(stubs):
    client = FakeClient(refresh_error=PlaudAuthError("refresh token rejected"))
    job, mails = make_job(client)
    job.run()
    assert stubs == [] and len(mails) == 1
