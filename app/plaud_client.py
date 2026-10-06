"""
Minimal client for Plaud's unofficial web API (D-001).

Only what voice-vault needs: list, detail (transcript + summary), trash and
permanent delete. Endpoints and payload shapes were taken from reading the
plaud-tools and plaud-api sources; they are not documented by Plaud.
"""
import base64
import gzip
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
TRANSCRIPT_TYPE = "transaction"
SUMMARY_TYPE = "auto_sum_note"
TASK_DONE = 1
PAGE_SIZE = 50
MAX_PAGES = 100
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
TIMEOUT_SECONDS = 30.0


class PlaudError(Exception):
    """Any failure talking to Plaud."""


class PlaudAuthError(PlaudError):
    """Token missing, malformed, expired or rejected (HTTP 401)."""


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


def token_expiry(token: str) -> Optional[int]:
    """Return the JWT `exp` claim (epoch seconds) or None if undecodable."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(padded)).get("exp")
    except (ValueError, TypeError):
        return None
    return int(exp) if isinstance(exp, (int, float)) else None


def is_plaud_host(host: str) -> bool:
    """True for an ASCII hostname under plaud.ai (guards the region redirect)."""
    if not host.isascii() or not host.endswith(".plaud.ai"):
        return False
    return all(l and all(c.isalnum() or c == "-" for c in l) for l in host.split("."))


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
        token: str,
        base_url: str = "https://api-euc1.plaud.ai",
        http: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = token
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._sleep = sleep

    # -- auth ---------------------------------------------------------
    def token_seconds_left(self, now_epoch: float) -> Optional[float]:
        exp = token_expiry(self._token)
        return None if exp is None else exp - now_epoch

    def _headers(self) -> dict:
        if not self._token:
            raise PlaudAuthError("PLAUD_TOKEN is not set")
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "app-platform": "web",
            "edit-from": "web",
        }

    # -- transport ----------------------------------------------------
    def _request(self, method: str, path: str, *, params=None, body=None, _redirected=False) -> dict:
        """Send one API call and return the parsed envelope (status == 0).

        Only GET is retried (429, 5xx, network errors); a mutating call gets one
        attempt so a transient error cannot repeat a side effect.
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
            if payload.get("status") == -302:
                return self._follow_redirect(method, path, payload, params, body, _redirected)
            if payload.get("status") != 0:
                raise PlaudError(f"{method} {path} failed: {payload.get('msg') or payload.get('status')}")
            return payload
        assert last is not None
        raise last

    def _follow_redirect(self, method, path, payload, params, body, already) -> dict:
        data = payload.get("data")
        domains = data.get("domains") if isinstance(data, dict) else None
        host = (domains or {}).get("api") if isinstance(domains, dict) else None
        host = urlparse(host if isinstance(host, str) and "//" in host else f"//{host}").netloc.lower()
        if already:
            raise PlaudError("region redirect loop")
        if not is_plaud_host(host):
            raise PlaudError("region redirect to a non-Plaud host refused")
        log.warning("Plaud region redirect old=%s new=%s", self.base_url, host)
        self.base_url = f"https://{host}"
        return self._request(method, path, params=params, body=body, _redirected=True)

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
