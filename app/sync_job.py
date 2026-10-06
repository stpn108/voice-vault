"""
One scheduled cycle: check the token, import, then delete (REQ-001).
The adapter (scheduler in main.py) only calls SyncJob.run().
"""
import datetime as dt
import logging
from typing import Callable

from sqlalchemy.orm import Session

from config import Config
from deletion_service import run_deletion
from import_service import run_import
from notify import send_mail
from plaud_client import PlaudAuthError, PlaudClient, PlaudError
from utils import now_utc

log = logging.getLogger(__name__)


class SyncJob:
    def __init__(self, cfg: Config, client: PlaudClient, session_factory: Callable[[], Session],
                 notifier: Callable[[Config, str, str], bool] = send_mail,
                 now_fn: Callable[[], dt.datetime] = now_utc):
        self._cfg = cfg
        self._client = client
        self._session_factory = session_factory
        self._notifier = notifier
        self._now_fn = now_fn
        self._last_mail_date: dt.date | None = None

    def _notify_once_a_day(self, subject: str, body: str) -> None:
        today = self._now_fn().date()
        if self._last_mail_date != today:
            self._last_mail_date = today
            self._notifier(self._cfg, subject, body)

    def check_token(self) -> None:
        """Warn when the token runs out soon; raise PlaudAuthError when it is expired."""
        left = self._client.token_seconds_left(self._now_fn().timestamp())
        if left is None:
            log.warning("Plaud token expiry cannot be read; relying on the API to reject it")
            return
        days = left / 86400
        if days <= 0:
            self._notify_once_a_day("voice-vault: Plaud token expired", "Sign in at web.plaud.ai and update PLAUD_TOKEN.")
            raise PlaudAuthError("Plaud token expired")
        if days < self._cfg.token_warn_days:
            log.warning("Plaud token expires soon days_left=%.1f", days)
            self._notify_once_a_day(
                "voice-vault: Plaud token expires soon",
                f"About {days:.1f} days left. Sign in at web.plaud.ai and update PLAUD_TOKEN.",
            )

    def run(self) -> None:
        log.info("Cycle start delete_enabled=%s", self._cfg.delete_enabled)
        try:
            self.check_token()
            run_import(self._session_factory, self._client, self._now_fn)
            run_deletion(self._session_factory, self._client, self._cfg, self._now_fn)
        except PlaudAuthError as exc:
            # Nothing is deleted after an auth failure: the run stops here.
            log.error("Cycle aborted: Plaud authentication failed (%s)", exc)
            self._notify_once_a_day("voice-vault: Plaud authentication failed", str(exc))
        except PlaudError as exc:
            log.error("Cycle aborted: Plaud error (%s)", exc)
        log.info("Cycle end")
