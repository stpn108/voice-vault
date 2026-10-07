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

FAILED_CYCLES_BEFORE_MAIL = 3


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
        self._failed_cycles = 0

    def _notify_once_a_day(self, subject: str, body: str) -> None:
        today = self._now_fn().date()
        if self._last_mail_date != today:
            self._last_mail_date = today
            self._notifier(self._cfg, subject, body)

    def check_token(self) -> None:
        """Renew the token if due, warn when the credential runs out soon, raise when it is dead."""
        now_epoch = self._now_fn().timestamp()
        self._client.ensure_fresh_token(now_epoch)
        left = self._client.credential_seconds_left(now_epoch)
        if left is None:
            log.warning("Plaud token expiry cannot be read; relying on the API to reject it")
            return
        days = left / 86400
        if days <= 0:
            self._notify_once_a_day(
                "voice-vault: Plaud token expired",
                "Get a new token and refresh token from web.plaud.ai and update .env (PLAUD_TOKEN, PLAUD_REFRESH_TOKEN).",
            )
            raise PlaudAuthError("Plaud token expired")
        if days < self._cfg.token_warn_days:
            log.warning("Plaud token expires soon days_left=%.1f", days)
            self._notify_once_a_day(
                "voice-vault: Plaud token expires soon",
                f"About {days:.1f} days left. Update PLAUD_TOKEN and PLAUD_REFRESH_TOKEN in .env from web.plaud.ai.",
            )

    def run(self) -> None:
        log.info("Cycle start delete_enabled=%s", self._cfg.delete_enabled)
        try:
            self.check_token()
            imported = run_import(self._session_factory, self._client, self._now_fn)
            self._count_failures(imported.failed > 0)
            run_deletion(self._session_factory, self._client, self._cfg, self._now_fn, imported)
        except PlaudAuthError as exc:
            # Nothing is deleted after an auth failure: the run stops here.
            log.error("Cycle aborted: Plaud authentication failed (%s)", exc)
            self._notify_once_a_day("voice-vault: Plaud authentication failed", str(exc))
        except PlaudError as exc:
            log.error("Cycle aborted: Plaud error (%s)", exc)
            self._count_failures(True)
        except Exception as exc:  # noqa: BLE001 - the scheduler would only log it; count it and tell the owner
            log.error("Cycle aborted: unexpected %s", type(exc).__name__)
            self._count_failures(True)
        log.info("Cycle end")

    def _count_failures(self, failed: bool) -> None:
        """Mail once a day when several cycles in a row had a problem; a log line alone goes unnoticed."""
        self._failed_cycles = self._failed_cycles + 1 if failed else 0
        if self._failed_cycles >= FAILED_CYCLES_BEFORE_MAIL:
            self._notify_once_a_day(
                "voice-vault: imports keep failing",
                f"{self._failed_cycles} cycles in a row had errors. Look at the logs of the app service.",
            )
