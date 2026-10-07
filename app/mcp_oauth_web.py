"""
OAuth endpoints of the MCP server (REQ-005, D-012): discovery documents, the owner's
login page, the token endpoint and the check for OAuth access tokens.

One pre-registered client (no dynamic registration), PKCE with S256 on every request,
the owner proves identity with a password, redirect URIs must match a fixed list.
"""
import asyncio
import base64
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, unquote, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import mcp_oauth
from config import Config, ConfigError, host_name
from strings import get_text
from utils import now_utc

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
FORM_TTL_SECONDS = 600
MIN_PASSWORD_CHARS = 20
MIN_CLIENT_ID_CHARS = 8
MAX_STATE_CHARS = 512
MAX_DELAY_SECONDS = 30
SCOPES = ["mcp", "offline_access"]
LOOPBACK = ("localhost", "127.0.0.1")
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
LOGIN_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"


@dataclass(frozen=True)
class OAuthSettings:
    issuer: str
    resource: str
    metadata_url: str
    client_id: str
    client_secret: str
    password: str
    redirect_uris: tuple


@dataclass
class OAuthRuntime:
    settings: OAuthSettings
    access_ok: Callable[[str], bool]


def build_settings(cfg: Config) -> OAuthSettings:
    """Validate the OAuth configuration; raises ConfigError for anything unsafe or incomplete."""
    if len(cfg.mcp_oauth_password) < MIN_PASSWORD_CHARS:
        raise ConfigError(f"MCP_OAUTH_PASSWORD needs at least {MIN_PASSWORD_CHARS} characters")
    if len(cfg.mcp_oauth_client_id) < MIN_CLIENT_ID_CHARS:
        raise ConfigError(f"MCP_OAUTH_CLIENT_ID needs at least {MIN_CLIENT_ID_CHARS} characters")
    public = urlparse(cfg.mcp_public_url)
    if not public.netloc or public.path not in ("", "/") or public.query or public.fragment:
        raise ConfigError("MCP_PUBLIC_URL must be the origin only, for example https://mcp.example.org")
    if public.scheme != "https" and not (public.scheme == "http" and public.hostname in LOOPBACK):
        raise ConfigError("MCP_PUBLIC_URL must use https")
    if not cfg.mcp_oauth_redirect_uris:
        raise ConfigError("MCP_OAUTH_REDIRECT_URIS is empty")
    for uri in cfg.mcp_oauth_redirect_uris:
        parsed = urlparse(uri)
        loopback_http = parsed.scheme == "http" and parsed.hostname in LOOPBACK
        if (parsed.scheme != "https" and not loopback_http) or not parsed.netloc or parsed.fragment or parsed.username:
            raise ConfigError(f"redirect URI not allowed: {uri}")
    base = cfg.mcp_public_url.rstrip("/")
    return OAuthSettings(
        issuer=base, resource=f"{base}/mcp", metadata_url=f"{base}/.well-known/oauth-protected-resource",
        client_id=cfg.mcp_oauth_client_id, client_secret=cfg.mcp_oauth_client_secret,
        password=cfg.mcp_oauth_password, redirect_uris=tuple(cfg.mcp_oauth_redirect_uris),
    )


def redirect_allowed(uri: str, allowed: tuple) -> bool:
    """Exact match; an allowed loopback entry without a port matches any port (RFC 8252)."""
    parsed = urlparse(uri or "")
    if not parsed.netloc or parsed.fragment or parsed.username:
        return False
    for entry in allowed:
        want = urlparse(entry)
        if uri == entry:
            return True
        if (want.hostname in LOOPBACK and want.port is None and parsed.scheme == want.scheme
                and parsed.hostname == want.hostname and parsed.path == want.path
                and parsed.query == want.query):
            return True
    return False


def _redirect(uri: str, **params: Optional[str]) -> RedirectResponse:
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items() if v is not None)
    separator = "&" if "?" in uri else "?"
    return RedirectResponse(f"{uri}{separator}{query}", status_code=302, headers=NO_STORE)


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def setup_oauth(app: FastAPI, settings: OAuthSettings, session_factory: Callable[[], Session], lang: str,
                sleep: Callable = asyncio.sleep) -> OAuthRuntime:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    form_secret = secrets.token_bytes(32)
    login_lock = asyncio.Lock()
    failures = {"count": 0}

    def t(key: str, **kwargs) -> str:
        return get_text(key, lang, **kwargs)

    def sign(payload: str) -> str:
        return hmac.new(form_secret, payload.encode(), "sha256").hexdigest()

    def page(request: Request, name: str, status: int = 200, **context):
        response = templates.TemplateResponse(request, name, {"t": t, "lang": lang, **context}, status_code=status)
        response.headers.update({**NO_STORE, "Content-Security-Policy": LOGIN_CSP, "Referrer-Policy": "no-referrer",
                                 "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"})
        return response

    def error_page(request: Request, key: str, status: int = 400):
        return page(request, "oauth_error.html", status, message=t(key))

    def login_page(request: Request, payload: dict, status: int = 200, error: bool = False):
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return page(request, "oauth_login.html", status, req=encoded, sig=sign(encoded), error=error,
                    redirect_host=urlparse(payload["redirect_uri"]).netloc)

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def protected_resource():
        return JSONResponse({
            "resource": settings.resource,
            "authorization_servers": [settings.issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp"],
        })

    @app.get("/.well-known/oauth-authorization-server")
    async def authorization_server():
        methods = ["client_secret_post", "client_secret_basic"] if settings.client_secret else ["none"]
        return JSONResponse({
            "issuer": settings.issuer,
            "authorization_endpoint": f"{settings.issuer}/authorize",
            "token_endpoint": f"{settings.issuer}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": methods,
            "scopes_supported": SCOPES,
            "authorization_response_iss_parameter_supported": True,
        })

    @app.get("/authorize")
    async def authorize_form(request: Request):
        q = request.query_params
        redirect_uri = q.get("redirect_uri", "")
        if not _same(q.get("client_id", ""), settings.client_id) or not redirect_allowed(redirect_uri, settings.redirect_uris):
            # Never redirect to an unverified address (RFC 6749 section 4.1.2.1).
            log.warning("OAuth authorize rejected: unknown client or redirect uri host=%s", urlparse(redirect_uri).netloc[:80])
            return error_page(request, "oauth_bad_request")
        state = q.get("state")
        if state is not None and len(state) > MAX_STATE_CHARS:
            return error_page(request, "oauth_bad_request")

        def fail(code: str):
            return _redirect(redirect_uri, error=code, state=state, iss=settings.issuer)

        if q.get("response_type") != "code":
            return fail("unsupported_response_type")
        if q.get("code_challenge_method") != "S256" or not mcp_oauth.is_valid_challenge(q.get("code_challenge", "")):
            return fail("invalid_request")
        if q.get("resource") and q.get("resource") != settings.resource:
            return fail("invalid_target")
        payload = {"redirect_uri": redirect_uri, "code_challenge": q["code_challenge"], "state": state,
                   "exp": int(time.time()) + FORM_TTL_SECONDS}
        return login_page(request, payload)

    @app.post("/authorize")
    async def authorize_submit(request: Request):
        form = await request.form()
        encoded, signature = str(form.get("req", "")), str(form.get("sig", ""))
        if not _same(sign(encoded), signature):
            log.warning("OAuth login rejected: form signature invalid")
            return error_page(request, "oauth_bad_request")
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded.encode()))
            redirect_uri, challenge, state = payload["redirect_uri"], payload["code_challenge"], payload.get("state")
            expires = int(payload["exp"])
        except (ValueError, KeyError, TypeError):
            return error_page(request, "oauth_bad_request")
        if expires < time.time():
            return error_page(request, "oauth_expired")
        if not redirect_allowed(redirect_uri, settings.redirect_uris) or not mcp_oauth.is_valid_challenge(challenge):
            return error_page(request, "oauth_bad_request")
        if form.get("action") == "deny":
            return _redirect(redirect_uri, error="access_denied", state=state, iss=settings.issuer)

        async with login_lock:  # one attempt at a time, each failure makes the next one slower
            if not _same(str(form.get("password", "")), settings.password):
                failures["count"] += 1
                delay = min(2 ** (failures["count"] - 1), MAX_DELAY_SECONDS)
                forwarded = re.sub(r"[^0-9a-fA-F:., ]", "", request.headers.get("x-forwarded-for", ""))[:80]
                log.warning("OAuth login failed attempts=%d forwarded_for=%s", failures["count"], forwarded)
                await sleep(delay)
                return login_page(request, payload, status=401, error=True)
            failures["count"] = 0

        def issue() -> str:
            with session_factory() as session:
                return mcp_oauth.issue_code(session, redirect_uri, challenge, now_utc())

        code = await run_in_threadpool(issue)
        log.info("OAuth authorization granted redirect_host=%s", urlparse(redirect_uri).netloc[:80])
        return _redirect(redirect_uri, code=code, state=state, iss=settings.issuer)

    def token_error(error: str, description: str, status: int = 400, headers: Optional[dict] = None):
        return JSONResponse({"error": error, "error_description": description}, status_code=status,
                            headers={**NO_STORE, **(headers or {})})

    @app.post("/token")
    async def token(request: Request):
        form = await request.form()
        client_id, client_secret = str(form.get("client_id", "")), str(form.get("client_secret", ""))
        basic = request.headers.get("authorization", "")
        if basic.lower().startswith("basic "):
            try:
                user, _, secret = base64.b64decode(basic[6:]).decode().partition(":")
                client_id, client_secret = unquote(user), unquote(secret)
            except (ValueError, UnicodeError):
                return token_error("invalid_client", "malformed basic credentials", 401, {"WWW-Authenticate": "Basic"})
        if not _same(client_id, settings.client_id) or (
                settings.client_secret and not _same(client_secret, settings.client_secret)):
            log.warning("OAuth token request rejected: client authentication failed")
            return token_error("invalid_client", "client authentication failed", 401, {"WWW-Authenticate": "Basic"})
        if form.get("resource") and form.get("resource") != settings.resource:
            return token_error("invalid_target", "unknown resource")
        grant = form.get("grant_type")

        def run() -> dict:
            with session_factory() as session:
                if grant == "authorization_code":
                    return mcp_oauth.exchange_code(session, str(form.get("code", "")), str(form.get("redirect_uri", "")),
                                                   str(form.get("code_verifier", "")), now_utc())
                return mcp_oauth.refresh_tokens(session, str(form.get("refresh_token", "")), now_utc())

        if grant == "authorization_code":
            if not all(form.get(k) for k in ("code", "redirect_uri", "code_verifier")):
                return token_error("invalid_request", "code, redirect_uri and code_verifier are required")
        elif grant == "refresh_token":
            if not form.get("refresh_token"):
                return token_error("invalid_request", "refresh_token is required")
        else:
            return token_error("unsupported_grant_type", "use authorization_code or refresh_token")
        try:
            result = await run_in_threadpool(run)
        except mcp_oauth.OAuthError as exc:
            log.info("OAuth token request refused grant=%s error=%s", grant, exc.error)
            return token_error(exc.error, exc.description, exc.status)
        log.info("OAuth tokens issued grant=%s", grant)
        return JSONResponse({**result, "scope": " ".join(SCOPES)}, headers=NO_STORE)

    def access_ok(token: str) -> bool:
        with session_factory() as session:
            return mcp_oauth.validate_access_token(session, token, now_utc())

    return OAuthRuntime(settings=settings, access_ok=access_ok)
