"""
Plaud session tokens and their automatic renewal (D-005, REQ-004).

Plaud's web app keeps a short-lived user token (cookie `pld_ut`, used as the
bearer) and a long-lived refresh token (cookie `pld_urt`). `POST
/auth/refresh-user-token` with the refresh token as cookie answers with new
cookies and may rotate the refresh token, so the current pair is persisted in
the database. The environment only seeds the first pair.
"""
import base64
import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from database import PlaudSession
from utils import now_utc

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
ACCESS_COOKIE = "pld_ut"
REFRESH_COOKIE = "pld_urt"
MIN_REFRESH_BUFFER_SECONDS = 900
BUFFER_FRACTION = 0.25
DEFAULT_LIFETIME_SECONDS = 86400


class PlaudError(Exception):
    """Any failure talking to Plaud."""


class PlaudAuthError(PlaudError):
    """Token missing, malformed, expired or rejected."""


def token_claims(token: str) -> Optional[dict]:
    """Decode the JWT payload without verifying it; None if undecodable."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None
    return claims if isinstance(claims, dict) else None


def token_expiry(token: str) -> Optional[int]:
    """Return the JWT `exp` claim (epoch seconds) or None if undecodable."""
    exp = (token_claims(token) or {}).get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def is_plaud_host(host: str) -> bool:
    """True for an ASCII hostname under plaud.ai (guards the region redirect)."""
    if not host.isascii() or not host.endswith(".plaud.ai"):
        return False
    return all(label and all(c.isalnum() or c == "-" for c in label) for label in host.split("."))


def redirect_host(payload: dict) -> Optional[str]:
    """Host from an in-body `status == -302` region redirect, or None if there is none."""
    if payload.get("status") != -302:
        return None
    data = payload.get("data")
    domains = data.get("domains") if isinstance(data, dict) else None
    api = domains.get("api") if isinstance(domains, dict) else None
    if not isinstance(api, str):
        raise PlaudError("region redirect without a target host")
    host = urlparse(api if "//" in api else f"//{api}").netloc.lower()
    if not is_plaud_host(host):
        raise PlaudError("region redirect to a non-Plaud host refused")
    return host


@dataclass
class StoredTokens:
    access_token: str
    refresh_token: Optional[str]
    seed_fingerprint: str


class TokenStore(Protocol):
    def load(self) -> Optional[StoredTokens]: ...
    def save(self, tokens: StoredTokens) -> None: ...


class DbTokenStore:
    """Single-row persistence of the current token pair (`plaud_sessions`)."""

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def load(self) -> Optional[StoredTokens]:
        with self._session_factory() as session:
            row = session.get(PlaudSession, 1)
            if row is None:
                return None
            return StoredTokens(row.access_token, row.refresh_token, row.seed_fingerprint)

    def save(self, tokens: StoredTokens) -> None:
        with self._session_factory() as session:
            row = session.get(PlaudSession, 1)
            if row is None:
                row = PlaudSession(id=1)
                session.add(row)
            row.access_token = tokens.access_token
            row.refresh_token = tokens.refresh_token
            row.seed_fingerprint = tokens.seed_fingerprint
            row.refreshed_at = now_utc()
            session.commit()


def _cookie(resp: httpx.Response, name: str) -> Optional[str]:
    """Last non-empty value of a Set-Cookie. Plaud sends a clearing `name=""` first."""
    found = None
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            value = header[len(name) + 1:].split(";")[0].strip().strip('"')
            if value:
                found = value
    return found


class PlaudAuth:
    def __init__(self, store: TokenStore, http: httpx.Client, seed_access: str = "",
                 seed_refresh: str = "", now_fn: Callable[[], dt.datetime] = now_utc):
        self._store = store
        self._http = http
        self._seed_access = seed_access.strip()
        self._seed_refresh = seed_refresh.strip()
        self._now_fn = now_fn
        self._tokens: Optional[StoredTokens] = None

    @property
    def tokens(self) -> StoredTokens:
        if self._tokens is None:
            self._tokens = self._load_or_seed()
        return self._tokens

    def _fingerprint(self) -> str:
        blob = f"{self._seed_access}\n{self._seed_refresh}".encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def _load_or_seed(self) -> StoredTokens:
        """The stored pair wins, unless the environment seed changed since it was stored."""
        stored = self._store.load()
        has_seed = bool(self._seed_access or self._seed_refresh)
        if has_seed and (stored is None or stored.seed_fingerprint != self._fingerprint()):
            seeded = StoredTokens(self._seed_access, self._seed_refresh or None, self._fingerprint())
            self._store.save(seeded)
            log.info("Plaud tokens seeded from environment has_refresh=%s", bool(seeded.refresh_token))
            return seeded
        return stored or StoredTokens("", None, "")

    def access_token(self) -> str:
        return self.tokens.access_token

    def can_refresh(self) -> bool:
        return bool(self.tokens.refresh_token)

    def access_seconds_left(self, now_epoch: float) -> Optional[float]:
        exp = token_expiry(self.tokens.access_token) if self.tokens.access_token else None
        return None if exp is None else exp - now_epoch

    def refresh_seconds_left(self, now_epoch: float) -> Optional[float]:
        token = self.tokens.refresh_token
        exp = token_expiry(token) if token else None
        return None if exp is None else exp - now_epoch

    def needs_refresh(self, now_epoch: float) -> bool:
        """True when there is no access token or it is close to its expiry."""
        if not self.tokens.access_token:
            return True
        claims = token_claims(self.tokens.access_token) or {}
        exp, iat = claims.get("exp"), claims.get("iat")
        if not isinstance(exp, (int, float)):
            return False  # unknown expiry: wait for a 401
        lifetime = exp - iat if isinstance(iat, (int, float)) and exp > iat else DEFAULT_LIFETIME_SECONDS
        return exp - now_epoch < max(MIN_REFRESH_BUFFER_SECONDS, BUFFER_FRACTION * lifetime)

    def refresh(self, base_url: str) -> str:
        """Exchange the refresh token for a new access token; return the base URL that worked."""
        latest = self._store.load()  # another process may have rotated the token
        if latest and latest.refresh_token:
            self._tokens = latest
        refresh = self.tokens.refresh_token
        if not refresh:
            raise PlaudAuthError("no refresh token available")
        for hop in range(2):
            resp = self._post_refresh(base_url, refresh)
            new_access = _cookie(resp, ACCESS_COOKIE)
            if new_access:
                new_refresh = _cookie(resp, REFRESH_COOKIE) or refresh
                self._tokens = StoredTokens(new_access, new_refresh, self.tokens.seed_fingerprint)
                self._store.save(self._tokens)
                log.info(
                    "Plaud token refreshed rotated=%s access_days_left=%s",
                    new_refresh != refresh,
                    _days(self.access_seconds_left(self._now_fn().timestamp())),
                )
                return base_url
            payload = _payload(resp)
            host = redirect_host(payload)
            if host and hop == 0:
                log.warning("Plaud region redirect during refresh new=%s", host)
                base_url = f"https://{host}"
                continue
            if host:
                raise PlaudAuthError("token refresh region redirect loop")
            msg = payload.get("msg") or f"status {payload.get('status')}"
            raise PlaudAuthError(f"token refresh returned no new token: {msg}")
        raise PlaudAuthError("token refresh region redirect loop")

    def _post_refresh(self, base_url: str, refresh: str) -> httpx.Response:
        try:
            resp = self._http.post(
                f"{base_url}/auth/refresh-user-token",
                headers={
                    "User-Agent": USER_AGENT,
                    "Origin": "https://web.plaud.ai",
                    "Referer": "https://web.plaud.ai/",
                    "Accept": "application/json, text/plain, */*",
                    "app-platform": "web",
                    "Cookie": f"{REFRESH_COOKIE}={refresh}",
                },
            )
        except httpx.HTTPError as exc:
            raise PlaudError(f"token refresh failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise PlaudAuthError(f"refresh token rejected (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise PlaudError(f"token refresh returned HTTP {resp.status_code}")
        return resp


def _payload(resp: httpx.Response) -> dict:
    try:
        payload = resp.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _days(seconds: Optional[float]) -> str:
    return "unknown" if seconds is None else f"{seconds / 86400:.1f}"
