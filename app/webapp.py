"""
Web UI to view, search and discard stored recordings (REQ-002, D-008).

No login: the port is bound to 127.0.0.1 and reached through the owner's SSH tunnel.
Instead the app defends against a hostile web page in the owner's browser:
host header check (DNS rebinding), CSRF token plus Origin check on every state
change, escaped output and a strict Content-Security-Policy.
"""
import hashlib
import hmac
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

import recording_service as svc
from config import Config, load_config
from database import engine, migrate_schema
from strings import get_text
from utils import LOCAL_TZ, now_utc, setup_logging

setup_logging()
log = logging.getLogger(__name__)

# Per process unless UI_SECRET is set: forms from before a restart are rejected.
CSRF_SECRET = (os.getenv("UI_SECRET") or secrets.token_hex(32)).encode()
LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
CSP = ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def csrf_token() -> str:
    return hmac.new(CSRF_SECRET, b"discard", hashlib.sha256).hexdigest()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def get_config() -> Config:
    return load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate_schema()
    yield


cfg_at_start = load_config()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS + list(cfg_at_start.ui_allowed_hosts))
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _localtime(value) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


def _hms(ms) -> str:
    total = int((ms or 0) // 1000)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _mmss(ms) -> str:
    if ms is None:
        return ""
    total = int(ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


templates.env.filters.update(localtime=_localtime, hms=_hms, mmss=_mmss)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def render(request: Request, cfg: Config, name: str, status_code: int = 200, **context) -> HTMLResponse:
    def t(key: str, **kwargs) -> str:
        return get_text(key, cfg.ui_lang, **kwargs)
    context.update(t=t, lang=cfg.ui_lang, csrf=csrf_token())
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def require_csrf(request: Request, token: str) -> None:
    """Reject state changes without our token or from another origin."""
    if not hmac.compare_digest(token or "", csrf_token()):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="origin mismatch")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = Query("", max_length=200), cursor: str = Query("", max_length=200),
          session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    try:
        page = svc.list_recordings(session, now_utc(), _stability(cfg), q, cursor or None)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid cursor")
    return render(request, cfg, "list.html", page=page, q=q)


@app.get("/recordings/{recording_id}", response_class=HTMLResponse)
def detail(request: Request, recording_id: int, session: Session = Depends(get_session),
           cfg: Config = Depends(get_config)):
    found = svc.get_detail(session, recording_id)
    if found is None:
        raise HTTPException(status_code=404, detail="recording not found")
    rec, segments = found
    state = svc.recording_state(rec, now_utc(), _stability(cfg))
    return render(request, cfg, "detail.html", rec=rec, segments=segments, state=state)


@app.get("/recordings/{recording_id}/discard", response_class=HTMLResponse)
def discard_confirm(request: Request, recording_id: int, session: Session = Depends(get_session),
                    cfg: Config = Depends(get_config)):
    found = svc.get_detail(session, recording_id)
    if found is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return render(request, cfg, "confirm_discard.html", rec=found[0])


@app.post("/recordings/{recording_id}/discard")
def discard_one(request: Request, recording_id: int, csrf: str = Form(""),
                session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    if not svc.discard(session, recording_id, now_utc()):
        raise HTTPException(status_code=404, detail="recording not found")
    return RedirectResponse("/", status_code=303)


@app.get("/cleanup", response_class=HTMLResponse)
def cleanup_preview(request: Request, days: int = Query(90, ge=1, le=3650),
                    session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    count = svc.count_older_than(session, days, now_utc())
    return render(request, cfg, "cleanup.html", days=days, count=count, changed=False)


@app.post("/cleanup")
def cleanup_run(request: Request, days: int = Form(..., ge=1, le=3650), expected_count: int = Form(...),
                csrf: str = Form(""), session: Session = Depends(get_session),
                cfg: Config = Depends(get_config)):
    require_csrf(request, csrf)
    now = now_utc()
    count = svc.count_older_than(session, days, now)
    if count != expected_count:
        # The set changed since the owner looked at it: show the new count, discard nothing.
        return render(request, cfg, "cleanup.html", status_code=409, days=days, count=count, changed=True)
    svc.discard_older_than(session, days, now)
    return RedirectResponse("/", status_code=303)


def _stability(cfg: Config) -> int:
    return cfg.stability_minutes
