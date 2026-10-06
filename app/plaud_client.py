"""
Minimal client for Plaud's unofficial web API (D-001).

Only what voice-vault needs: list, detail (transcript + summary), trash and
permanent delete. Endpoints and payload shapes were taken from reading the
plaud-tools and plaud-api sources; they are not documented by Plaud.
"""
import gzip
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
from sqlalchemy.orm import Session

from plaud_auth import (
    DbTokenStore, PlaudAuth, PlaudAuthError, PlaudError, USER_AGENT, is_plaud_host,
    redirect_host, token_expiry,
)

log = logging.getLogger(__name__)

TRANSCRIPT_TYPE = "transaction"
SUMMARY_TYPE = "auto_sum_note"
TASK_DONE = 1
PAGE_SIZE = 50
MAX_PAGES = 100
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
TIMEOUT_SECONDS = 30.0


@dataclass
class PlaudRecording:
    plaud_id: str
    title: str
    start_ms: int
    duration_ms: int
    is_trash: bool = False


@dataclass
class PlaudDetail:
    plaud_id: str
    title: str
    start_ms: int
    duration_ms: int
    is_processed: bool
    raw: dict = field(default_factory=dict, repr=False)


def _content_item(raw: dict, data_type: str) -> Optional[dict]:
    for item in raw.get("content_list") or []:
        if isinstance(item, dict) and item.get("data_type") == data_type:
            return item
    return None


def _summary_from_obj(obj: Any) -> Optional[str]:
    """Extract markdown from the variants Plaud uses for the summary body."""
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped.startswith("{"):
            try:
                return _summary_from_obj(json.loads(stripped)) or obj
            except ValueError:
                return obj
        return obj or None
    if isinstance(obj, dict):
        for key in ("ai_content", "markdown", "content", "text", "summary"):
            val = obj.get(key)
            found = _summary_from_obj(val) if isinstance(val, (str, dict)) else None
            if found:
                return found
    return None


class PlaudClient:
    def __init__(
        self,
        auth: PlaudAuth,
        base_url: str = "https://api-euc1.plaud.ai",
        http: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._auth = auth
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._sleep = sleep

    # -- auth ---------------------------------------------------------
    def ensure_fresh_token(self, now_epoch: float) -> None:
        """Renew the access token ahead of its expiry. Raises PlaudAuthError if none is usable."""
        if not self._auth.needs_refresh(now_epoch):
            return
        if not self._auth.can_refresh():
            if not self._auth.access_token():
                raise PlaudAuthError("no Plaud token configured")
            return
        try:
            self.base_url = self._auth.refresh(self.base_url)
        except PlaudAuthError:
            raise
        except PlaudError as exc:
            left = self._auth.access_seconds_left(now_epoch)
            if left is not None and left > 0:
                log.warning("Token refresh failed, using the current token reason=%s", exc)
                return
            raise PlaudAuthError(f"token refresh failed and the access token is unusable: {exc}") from exc

    def credential_seconds_left(self, now_epoch: float) -> Optional[float]:
        """Seconds until the credential that keeps the job running dies.

        With a refresh token that is the refresh token; without one the access token.
        """
        if self._auth.can_refresh():
            return self._auth.refresh_seconds_left(now_epoch)
        return self._auth.access_seconds_left(now_epoch)

    def _headers(self) -> dict:
        token = self._auth.access_token()
        if not token:
            raise PlaudAuthError("no Plaud token configured")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "app-platform": "web",
            "edit-from": "web",
        }

    # -- transport ----------------------------------------------------
    def _request(self, method: str, path: str, *, params=None, body=None,
                 _redirected=False, _refreshed=False) -> dict:
        """Send one API call and return the parsed envelope (status == 0).

        Only GET is retried (429, 5xx, network errors); a mutating call gets one
        attempt so a transient error cannot repeat a side effect. A 401 means
        the call was rejected before it ran, so after one token refresh every
        method is sent once more.
        """
        attempts = MAX_ATTEMPTS if method == "GET" else 1
        last: Optional[Exception] = None
        for attempt in range(attempts):
            if attempt:
                self._sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
            try:
                resp = self._http.request(
                    method, f"{self.base_url}{path}", params=params, json=body,
                    headers=self._headers(),
                )
            except httpx.HTTPError as exc:
                last = PlaudError(f"{method} {path} failed: {type(exc).__name__}: {exc}")
                continue
            if resp.status_code == 401:
                if not _refreshed and self._auth.can_refresh():
                    log.info("Plaud rejected the token, refreshing path=%s", path)
                    self.base_url = self._auth.refresh(self.base_url)
                    return self._request(method, path, params=params, body=body,
                                         _redirected=_redirected, _refreshed=True)
                raise PlaudAuthError(f"{method} {path} rejected the token (HTTP 401)")
            if resp.status_code == 429 or resp.status_code >= 500:
                last = PlaudError(f"{method} {path} returned HTTP {resp.status_code}")
                continue
            if resp.status_code >= 400:
                raise PlaudError(f"{method} {path} returned HTTP {resp.status_code}")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise PlaudError(f"{method} {path} returned a non-JSON body") from exc
            if not isinstance(payload, dict):
                raise PlaudError(f"{method} {path} returned a non-object payload")
            host = redirect_host(payload)
            if host:
                if _redirected:
                    raise PlaudError("region redirect loop")
                log.warning("Plaud region redirect old=%s new=%s", self.base_url, host)
                self.base_url = f"https://{host}"
                return self._request(method, path, params=params, body=body,
                                     _redirected=True, _refreshed=_refreshed)
            if payload.get("status") != 0:
                raise PlaudError(f"{method} {path} failed: {payload.get('msg') or payload.get('status')}")
            return payload
        assert last is not None
        raise last

    def _download(self, url: str) -> Any:
        """Fetch a presigned link (no auth header) and parse it as JSON or text."""
        if not url.startswith("https://"):
            raise PlaudError("refusing to download from a non-https link")
        try:
            resp = self._http.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            raise PlaudError(f"download failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise PlaudError(f"download returned HTTP {resp.status_code}")
        body = resp.content
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8")
        try:
            return json.loads(text)
        except ValueError:
            return text

    # -- endpoints ----------------------------------------------------
    def list_recordings(self) -> list[PlaudRecording]:
        """All recordings outside the Plaud trash, newest first."""
        result: list[PlaudRecording] = []
        for page in range(MAX_PAGES):
            data = self._request("GET", "/file/simple/web", params={
                "skip": page * PAGE_SIZE, "limit": PAGE_SIZE, "is_trash": 0,
                "sort_by": "start_time", "is_desc": "true",
            })
            items = data.get("data_file_list") or data.get("data") or []
            for item in items:
                if item.get("is_trash"):
                    continue
                result.append(PlaudRecording(
                    plaud_id=str(item.get("id") or item.get("file_id") or ""),
                    title=str(item.get("filename") or item.get("file_name") or ""),
                    start_ms=int(item.get("start_time") or 0),
                    duration_ms=int(item.get("duration") or 0),
                ))
            if len(items) < PAGE_SIZE:
                break
        return [r for r in result if r.plaud_id]

    def get_detail(self, plaud_id: str) -> PlaudDetail:
        payload = self._request("GET", f"/file/detail/{plaud_id}")
        raw = payload.get("data", payload)
        if not isinstance(raw, dict):
            raise PlaudError(f"detail for {plaud_id} has an unexpected shape")
        return PlaudDetail(
            plaud_id=plaud_id,
            title=str(raw.get("file_name") or raw.get("filename") or ""),
            start_ms=int(raw.get("start_time") or 0),
            duration_ms=int(raw.get("duration") or 0),
            is_processed=self.is_processed(raw),
            raw=raw,
        )

    @staticmethod
    def is_processed(raw: dict) -> bool:
        """Transcript and summary both finished (`task_status == 1`); otherwise False."""
        transcript = _content_item(raw, TRANSCRIPT_TYPE)
        summary = _content_item(raw, SUMMARY_TYPE)
        if not transcript or transcript.get("task_status") != TASK_DONE or not transcript.get("data_link"):
            return False
        return bool(summary and summary.get("task_status") == TASK_DONE)

    def fetch_segments(self, detail: PlaudDetail) -> list[dict]:
        """Transcript utterances: speaker, content, start_time/end_time in ms."""
        item = _content_item(detail.raw, TRANSCRIPT_TYPE)
        if not item or not item.get("data_link"):
            raise PlaudError(f"recording {detail.plaud_id} has no transcript link")
        body = self._download(str(item["data_link"]))
        if isinstance(body, dict):
            body = body.get("trans_result")
        if not isinstance(body, list):
            raise PlaudError(f"transcript of {detail.plaud_id} has an unexpected shape")
        return [s for s in body if isinstance(s, dict)]

    def fetch_summary(self, detail: PlaudDetail) -> str:
        """AI summary as markdown text. Empty string if Plaud sent none."""
        item = _content_item(detail.raw, SUMMARY_TYPE)
        if not item:
            return ""
        for pre in detail.raw.get("pre_download_content_list") or []:
            if isinstance(pre, dict) and (
                pre.get("data_id") == item.get("data_id") or pre.get("data_type") == SUMMARY_TYPE
            ):
                found = _summary_from_obj(pre.get("data_content"))
                if found:
                    return found
        if item.get("data_link"):
            return _summary_from_obj(self._download(str(item["data_link"]))) or ""
        return ""

    def trash(self, plaud_ids: list[str]) -> None:
        if not plaud_ids:
            raise ValueError("plaud_ids must not be empty")
        self._request("POST", "/file/trash/", body=plaud_ids)

    def delete_permanently(self, plaud_ids: list[str]) -> None:
        if not plaud_ids:
            raise ValueError("plaud_ids must not be empty")
        self._request("DELETE", "/file/", body=plaud_ids)


def build_client(cfg, session_factory: Callable[[], Session]) -> PlaudClient:
    """Wire the client with database-backed tokens seeded from the environment."""
    http = httpx.Client(timeout=TIMEOUT_SECONDS)
    auth = PlaudAuth(DbTokenStore(session_factory), http, cfg.plaud_token, cfg.plaud_refresh_token)
    return PlaudClient(auth, cfg.plaud_api_base, http=http)
