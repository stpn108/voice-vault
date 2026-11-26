"""
Minimal client for Plaud's unofficial web API (D-001).

Only what voice-vault needs: list, detail (transcript + summary), trash and
permanent delete, all on the Plaud 4.0 routes under `/file-app/v4/` (D-080).
Endpoints and payload shapes were taken from reading the plaud-tools, chatterbox
and OpenAudioHub sources; they are not documented by Plaud.
"""
import ipaddress
import json
import logging
import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy.orm import Session

from plaud_auth import (
    DbTokenStore, PlaudAuth, PlaudAuthError, PlaudError, USER_AGENT, is_plaud_host,
    redirect_host, token_expiry,
)

log = logging.getLogger(__name__)

V4 = "/file-app/v4"
TRANSCRIPT_TYPE = "TRANSCRIPT"
POLISHED_TYPE = "POLISHED_TRANSCRIPT"
SUMMARY_TYPE = "SUMMARY"
# A migrated object reports task_status null once done, a generated one 1.
DONE_TASK_STATUSES = (None, 1)
# Plaud 4.0 names a recording made before the move `of_<old id>`, its container folder `on_w_<old id>`.
LEGACY_FILE_PREFIX = "of_"
LEGACY_CONTAINER_PREFIX = "on_w_"
PAGE_SIZE = 50
MAX_PAGES = 100
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
TIMEOUT_SECONDS = 30.0
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Recordings before 2015 do not exist; a start time of 0 or garbage means the API changed shape.
MIN_PLAUSIBLE_START_MS = 1_420_070_400_000
ERROR_BODY_PREFIXES = ("<?xml", "<error", "<html", "<!doctype")


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


def _segments(value: Any) -> list[dict]:
    """Utterances from the shapes Plaud uses: a list, {"trans_result": [...]}, or that as a JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return []
    if isinstance(value, dict):
        value = value.get("trans_result") or value.get("data") or value.get("segments")
    if not isinstance(value, list):
        return []
    return [s for s in value if isinstance(s, dict)]


def _object(raw: dict, object_type: str) -> Optional[dict]:
    for item in raw.get("objects") or []:
        if isinstance(item, dict) and item.get("object_type") == object_type:
            return item
    return None


def _object_done(item: Optional[dict]) -> bool:
    """A content object is finished when it has a download link and its task is done."""
    return bool(item and item.get("content_url") and item.get("task_status") in DONE_TASK_STATUSES)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _summary_from_obj(obj: Any) -> Optional[str]:
    """Extract markdown from the variants Plaud uses for the summary body."""
    if isinstance(obj, str):
        stripped = obj.strip()
        if stripped.startswith("{"):
            try:
                return _summary_from_obj(json.loads(stripped)) or obj
            except ValueError:
                return obj
        return obj if stripped else None
    if isinstance(obj, dict):
        for key in ("ai_content", "markdown", "content", "text", "summary"):
            val = obj.get(key)
            found = _summary_from_obj(val) if isinstance(val, (str, dict)) else None
            if found:
                return found
    return None


def _check_id(plaud_id: str) -> None:
    if not isinstance(plaud_id, str) or not ID_RE.fullmatch(plaud_id):
        raise PlaudError("a Plaud id has an unusable form")


def _local_id(file_id: str) -> str:
    """The id voice-vault stores: a recording from before Plaud 4.0 keeps its old id (D-080)."""
    return file_id[len(LEGACY_FILE_PREFIX):] if file_id.startswith(LEGACY_FILE_PREFIX) else file_id


def _file_id(plaud_id: str) -> str:
    """The Plaud 4.0 file id of a stored id. Old ids are plain hex and get `of_`; 4.0 ids already have a prefix."""
    _check_id(plaud_id)
    return plaud_id if "_" in plaud_id else LEGACY_FILE_PREFIX + plaud_id


def _check_download_url(url: str) -> None:
    """Presigned links: https and a real host name, never an address or a bare internal name."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https":
        raise PlaudError("refusing to download from a non-https link")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal or "." not in host or host.endswith(".local") or host.endswith(".internal") or parsed.username:
        raise PlaudError("refusing to download from this host")


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
    def reload_tokens(self) -> None:
        self._auth.reload()

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

    def credential_warn_seconds(self, warn_days: float) -> float:
        """How long before expiry to warn (D-068): never while a refresh token renews it, else `warn_days`."""
        return 0 if self._auth.can_refresh() else warn_days * 86400

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
        _check_download_url(url)
        try:
            with self._http.stream("GET", url, headers={"User-Agent": USER_AGENT}) as resp:
                # Only a plain 200 is content: a 3xx or an error page must never be stored as a transcript.
                if resp.status_code != 200:
                    raise PlaudError(f"download returned HTTP {resp.status_code}")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOWNLOAD_BYTES:
                        raise PlaudError("download is larger than the allowed size")
        except httpx.HTTPError as exc:
            raise PlaudError(f"download failed: {type(exc).__name__}: {exc}") from exc
        data = bytes(body)
        try:
            if data[:2] == b"\x1f\x8b":
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = inflater.decompress(data, MAX_DOWNLOAD_BYTES + 1)
                if len(data) > MAX_DOWNLOAD_BYTES or not inflater.eof:
                    raise PlaudError("download is larger than the allowed size or truncated")
            text = data.decode("utf-8")
        except (zlib.error, UnicodeDecodeError) as exc:
            raise PlaudError(f"download is not valid text: {type(exc).__name__}") from exc
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            if text.lstrip().lower().startswith(ERROR_BODY_PREFIXES):
                raise PlaudError("download is an error page, not content") from None
            return text

    # -- endpoints ----------------------------------------------------
    def _walk(self, path: str, params: dict) -> list[dict]:
        """Every item of a Plaud 4.0 list endpoint, following `next_cursor`."""
        items: list[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            query = {**params, "page_size": PAGE_SIZE, **({"cursor": cursor} if cursor else {})}
            data = _dict(self._request("GET", f"{V4}{path}", params=query).get("data"))
            page = data.get("items")
            if not isinstance(page, list):
                # A changed shape must not read as "no recordings".
                raise PlaudError(f"GET {V4}{path} returned no item list")
            items.extend(item for item in page if isinstance(item, dict))
            cursor = data.get("next_cursor") or None
            if not cursor:
                break
        return items

    def _trash_entries(self) -> dict[str, dict]:
        """Recordings in the Plaud trash: stored id -> the node a permanent delete names (D-042, D-080).

        The trash holds a recording's container folder. One from before 4.0 (`on_w_<old id>`) names
        its recording; a newer one is opened once to find it. Trashed folders of the owner are skipped.
        """
        entries: dict[str, dict] = {}
        for item in self._walk("/folders/content", {"scope": "trash"}):
            node_id = str(item.get("node_id") or "")
            if item.get("type") == 2 and item.get("file_id"):
                plaud_id = _local_id(str(item["file_id"]))
            elif item.get("type") == 1 and not item.get("icon") and not item.get("system_folder_type"):
                if node_id.startswith(LEGACY_CONTAINER_PREFIX):
                    plaud_id = node_id[len(LEGACY_CONTAINER_PREFIX):]
                else:
                    plaud_id = _local_id(self._container_file_id(node_id))
            else:
                continue
            if node_id and ID_RE.fullmatch(plaud_id):
                entries[plaud_id] = {"node_id": node_id, "origin_version": item.get("version_ms")}
        return entries

    def _container_file_id(self, container_id: str) -> str:
        """File id of the recording in a container folder; it may also hold notes (`MD`). '' if none."""
        files = [f for f in self._walk("/folders/children", {"node_id": container_id}) if f.get("file_id")]
        recordings = [f for f in files if f.get("file_type") == "REC"]
        not_notes = [f for f in files if f.get("file_type") != "MD"]
        return str((recordings or not_notes or [{}])[0].get("file_id") or "")

    def list_trash_ids(self) -> set[str]:
        """Ids of the recordings in the Plaud trash (D-042)."""
        return set(self._trash_entries())

    def list_recordings(self) -> list[PlaudRecording]:
        """All recordings outside the Plaud trash, newest first."""
        result: list[PlaudRecording] = []
        for item in self._walk("/recordings/all", {"sort_by": "created", "sort_order": "desc"}):
            try:
                if item.get("is_trash") or item.get("trash_info"):
                    continue
                plaud_id = _local_id(str(item.get("file_id") or ""))
                if not ID_RE.fullmatch(plaud_id):
                    log.warning("Skipping a listed recording with an unusable id")
                    continue
                result.append(PlaudRecording(
                    plaud_id=plaud_id,
                    title=str(item.get("name") or ""),
                    start_ms=int(item.get("created_at_show_ms") or 0),
                    duration_ms=int(item.get("duration_ms") or 0),
                ))
            except (AttributeError, TypeError, ValueError, OverflowError):
                log.warning("Skipping a listed recording with an unexpected shape")
        return result

    def get_detail(self, plaud_id: str) -> PlaudDetail:
        payload = self._request("GET", f"{V4}/files/detail/{quote(_file_id(plaud_id), safe='')}")
        raw = payload.get("data")
        if not isinstance(raw, dict):
            raise PlaudError(f"detail for {plaud_id} has an unexpected shape")
        meta = _dict(raw.get("meta"))
        try:
            start_ms, duration_ms = int(meta.get("start_time") or 0), int(meta.get("duration") or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PlaudError(f"detail for {plaud_id} has an unreadable start time or duration") from exc
        return PlaudDetail(
            plaud_id=plaud_id,
            title=str(_dict(raw.get("node")).get("name") or ""),
            start_ms=start_ms,
            duration_ms=duration_ms,
            is_processed=self.is_processed(raw),
            raw=raw,
        )

    @staticmethod
    def is_processed(raw: dict) -> bool:
        """Transcript and summary both finished; otherwise False."""
        return _object_done(_object(raw, TRANSCRIPT_TYPE)) and _object_done(_object(raw, SUMMARY_TYPE))

    def fetch_segments(self, detail: PlaudDetail) -> list[dict]:
        """Transcript utterances: speaker, content, start_time/end_time in ms.

        Plaud keeps two transcripts. The cleaned one ("Transkript bereinigt", `POLISHED_TRANSCRIPT`)
        is read first (D-026); the original one (`TRANSCRIPT`) is the logged fallback (D-080).
        """
        polished = self._download_block(detail, POLISHED_TYPE)
        if polished:
            return polished
        log.warning("Cleaned transcript not available, using the original one plaud_id=%s", detail.plaud_id)
        item = _object(detail.raw, TRANSCRIPT_TYPE)
        if not _object_done(item):
            raise PlaudError(f"recording {detail.plaud_id} has no readable transcript")
        body = self._download(str(item["content_url"]))
        if isinstance(body, dict):
            body = body.get("trans_result")
        if not isinstance(body, list):
            raise PlaudError(f"transcript of {detail.plaud_id} has an unexpected shape")
        return [s for s in body if isinstance(s, dict)]

    def _download_block(self, detail: PlaudDetail, object_type: str) -> list[dict]:
        """Segments of a finished transcript object from its download link; [] if it is not finished."""
        item = _object(detail.raw, object_type)
        if not _object_done(item):
            return []
        return _segments(self._download(str(item["content_url"])))

    def fetch_summary(self, detail: PlaudDetail) -> str:
        """AI summary as markdown text. Empty string if Plaud sent none."""
        item = _object(detail.raw, SUMMARY_TYPE)
        if not _object_done(item):
            return ""
        try:
            return _summary_from_obj(self._download(str(item["content_url"]))) or ""
        except RecursionError as exc:
            raise PlaudError("summary is nested too deeply") from exc

    def _movable_node(self, plaud_id: str) -> dict:
        """`{node_id, origin_version}` of what moves as the recording: its container folder, else its file node."""
        node = _dict(self.get_detail(plaud_id).raw.get("node"))
        container = _dict(node.get("folder")).get("folder_id")
        shown_in = _dict(node.get("display_folder")).get("folder_id")
        if not container or container == shown_in:
            if not node.get("node_id"):
                raise PlaudError(f"detail for {plaud_id} names no node")
            return {"node_id": node["node_id"], "origin_version": node.get("version_ms")}
        params = {"file_id": _file_id(plaud_id)}
        data = _dict(self._request("GET", f"{V4}/nodes/ancestor-items", params=params).get("data"))
        siblings = [i for i in data.get("flat_items") or [] if isinstance(i, dict)]
        if not any(i.get("node_id") == container for i in siblings) and shown_in:
            siblings = self._walk("/folders/children", {"node_id": shown_in})
        for item in siblings:
            if item.get("node_id") == container:
                return {"node_id": container, "origin_version": item.get("version_ms")}
        raise PlaudError(f"container folder of {plaud_id} not found")

    def _node_batch(self, path: str, items: list[dict]) -> None:
        """A 4.0 node write. It answers status 0 even when an item failed and lists those in `data.failed`."""
        data = _dict(self._request("DELETE", f"{V4}{path}", body={"items": items}).get("data"))
        failed = [_dict(f) for f in data.get("failed") or []]
        if failed:
            reasons = "; ".join(f"{f.get('node_id')}: {f.get('error_message') or f.get('error_code')}" for f in failed)
            raise PlaudError(f"DELETE {V4}{path} failed for {len(failed)} item(s): {reasons}")

    def trash(self, plaud_ids: list[str]) -> None:
        if not plaud_ids:
            raise ValueError("plaud_ids must not be empty")
        for plaud_id in plaud_ids:
            _check_id(plaud_id)
        self._node_batch("/nodes/trash", [self._movable_node(plaud_id) for plaud_id in plaud_ids])

    def delete_permanently(self, plaud_ids: list[str]) -> None:
        """Delete recordings from the Plaud trash for good. One that is not in the trash is refused (D-080)."""
        if not plaud_ids:
            raise ValueError("plaud_ids must not be empty")
        for plaud_id in plaud_ids:
            _check_id(plaud_id)
        trashed = self._trash_entries()
        missing = [plaud_id for plaud_id in plaud_ids if plaud_id not in trashed]
        if missing:
            raise PlaudError(f"not in the Plaud trash, not deleted: {', '.join(missing)}")
        self._node_batch("/nodes/delete", [trashed[plaud_id] for plaud_id in plaud_ids])


def build_client(cfg, session_factory: Callable[[], Session]) -> PlaudClient:
    """Wire the client with database-backed tokens seeded from the environment."""
    http = httpx.Client(timeout=TIMEOUT_SECONDS)
    auth = PlaudAuth(DbTokenStore(session_factory), http, cfg.plaud_token, cfg.plaud_refresh_token)
    return PlaudClient(auth, cfg.plaud_api_base, http=http)
