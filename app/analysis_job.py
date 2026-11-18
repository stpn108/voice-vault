"""
Scheduled adapter for the server-side analysis (REQ-007, D-072).
The scheduler in main.py calls AnalysisJob.run(); the work is in analysis_service.
"""
import datetime as dt
import logging
from typing import Callable, Optional

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from analysis_service import Settings, run_analysis
from config import Config
from llm_client import build_client
from notify import send_mail
from utils import now_utc

log = logging.getLogger(__name__)


class AnalysisJob:
    def __init__(self, cfg: Config, llm, session_factory: Callable[[], Session],
                 notifier: Callable[[Config, str, str], bool] = send_mail,
                 now_fn: Callable[[], dt.datetime] = now_utc):
        self._cfg = cfg
        self._llm = llm
        self._session_factory = session_factory
        self._notifier = notifier
        self._now_fn = now_fn
        self._settings = Settings(cfg.stability_minutes, cfg.analysis_lookback_days, cfg.analysis_max_recordings,
                                  cfg.analysis_max_requests)
        self._last_mail_date: Optional[dt.date] = None

    def _notify_once_a_day(self, subject: str, body: str) -> None:
        today = self._now_fn().date()
        if self._last_mail_date != today:
            self._last_mail_date = today
            self._notifier(self._cfg, subject, body)

    def run(self) -> None:
        try:
            result = run_analysis(self._session_factory, self._llm, self._now_fn, self._settings)
        except OperationalError as exc:
            # The database is away for a moment (D-070): one line, the next run tries again.
            log.warning("Database not reachable, analysis tried again in the next run reason=%s",
                        str(exc.orig or exc).strip().splitlines()[0][:120])
            return
        except Exception as exc:  # noqa: BLE001 - the scheduler would only log it; tell the owner
            log.exception("Analysis run aborted: unexpected %s", type(exc).__name__)
            self._notify_once_a_day("voice-vault: analysis failed",
                                    f"The analysis stopped with {type(exc).__name__}. Look at the logs of the app service.")
            return
        if result is not None and result.status != "ok":
            self._notify_once_a_day(
                "voice-vault: analysis failed",
                f"Analysis run {result.run_id} ended with status {result.status} ({result.error}). "
                f"Recordings not marked analyzed ({sorted(set(result.recording_ids) - set(result.done_ids))}) "
                "come again in the next run; after two failed runs they wait 24 hours. "
                "Look at the logs of the app service.",
            )


def build_analysis_job(cfg: Config, session_factory: Callable[[], Session]) -> Optional[AnalysisJob]:
    """The job, or None when no API key is configured (REQ-007 criterion 1)."""
    llm = build_client(cfg.anthropic_api_key, cfg.analysis_model, cfg.analysis_effort)
    if llm is None:
        log.info("Analysis off: ANTHROPIC_API_KEY is not set")
        return None
    log.info("Analysis on model=%s effort=%s every %d min, at most %d recordings and %d requests per run",
             cfg.analysis_model, cfg.analysis_effort, cfg.import_interval_minutes, cfg.analysis_max_recordings,
             cfg.analysis_max_requests)
    return AnalysisJob(cfg, llm, session_factory)
