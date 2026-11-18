"""The scheduled analysis job (REQ-007): off without a key, its own scheduler job, one mail a day on failures."""
import dataclasses
import datetime as dt
import logging

from analysis_job import AnalysisJob, build_analysis_job
from config import load_config
from llm_client import LlmError
from main import build_scheduler
from tests.analysis_helpers import ScriptedLlm
from tests.helpers import add_recording

NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.timezone.utc)


def cfg(**overrides):
    return dataclasses.replace(load_config(), **overrides)


class StubSync:
    def run(self):
        pass


def test_req_007_analysis_is_off_without_an_api_key(caplog):
    with caplog.at_level(logging.INFO, logger="analysis_job"):
        assert build_analysis_job(cfg(anthropic_api_key=""), session_factory=None) is None
    assert "Analysis off: ANTHROPIC_API_KEY is not set" in caplog.text


def test_req_007_analysis_runs_in_its_own_scheduler_job():
    job = AnalysisJob(cfg(), ScriptedLlm(), session_factory=None)
    scheduler = build_scheduler(StubSync(), 10, job)
    analysis = scheduler.get_job("analysis")
    assert analysis.func == job.run and analysis.max_instances == 1
    assert analysis.trigger.interval == dt.timedelta(minutes=10)
    assert scheduler.get_job("plaud_sync").func != job.run


def test_req_007_no_analysis_job_without_an_api_key():
    assert build_scheduler(StubSync(), 10, None).get_job("analysis") is None


def test_req_007_failed_runs_send_at_most_one_mail_a_day(session_factory):
    with session_factory() as session:
        add_recording(session, index=1, started=NOW - dt.timedelta(hours=2))
    mails = []
    llm = ScriptedLlm(LlmError("failed", "OverloadedError status=529"), LlmError("failed", "OverloadedError status=529"))
    job = AnalysisJob(cfg(), llm, session_factory, notifier=lambda c, subject, body: mails.append(subject) or True,
                      now_fn=lambda: NOW)
    job.run()
    job.run()
    assert mails == ["voice-vault: analysis failed"] and len(llm.calls) == 2
