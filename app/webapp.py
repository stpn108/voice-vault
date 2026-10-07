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
from typing import Annotated, Iterator

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

import recording_service as svc
import todo_service as todos
import topic_admin
from config import Config, load_config
from database import engine, migrate_schema
from markdown_lite import render_markdown
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
MAX_ID = svc.MAX_ID  # ids beyond the database column are rejected before they reach a query


def csrf_token() -> str:
    return hmac.new(CSRF_SECRET, b"discard", hashlib.sha256).hexdigest()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def get_config() -> Config:
    return load_config()


def cfg_lang() -> str:
    return load_config().ui_lang


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


templates.env.filters.update(localtime=_localtime, hms=_hms, mmss=_mmss, md=render_markdown)


def secure(response):
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # "no-referrer" would make browsers send `Origin: null` with every form post. "same-origin" sends
    # no referrer to other sites either, but our own forms carry their real origin.
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    return secure(await call_next(request))


@app.exception_handler(StarletteHTTPException)
async def error_page(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
    """A readable page instead of bare JSON; the status code stays."""
    cfg = load_config()
    key = {403: "ui_error_forbidden", 404: "ui_error_not_found"}.get(exc.status_code, "ui_error_invalid")
    return render(request, cfg, "error.html", status_code=exc.status_code, headline=get_text(key, cfg.ui_lang),
                  detail=str(exc.detail) if exc.status_code in (400, 409) else "")


@app.exception_handler(RequestValidationError)
async def invalid_input_page(request: Request, exc: RequestValidationError) -> HTMLResponse:
    cfg = load_config()
    return render(request, cfg, "error.html", status_code=400, headline=get_text("ui_error_invalid", cfg.ui_lang),
                  detail="")


@app.exception_handler(Exception)
async def unexpected_error_page(request: Request, exc: Exception) -> HTMLResponse:
    """The outermost handler sits outside the middleware, so the security headers are set here too."""
    log.error("Unhandled error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return secure(HTMLResponse("<!doctype html><title>Error</title><p>Internal Server Error</p>", status_code=500))


def render(request: Request, cfg: Config, name: str, status_code: int = 200, **context) -> HTMLResponse:
    def t(key: str, **kwargs) -> str:
        return get_text(key, cfg.ui_lang, **kwargs)
    plaud_note = t(
        "ui_discard_plaud_on" if cfg.delete_enabled else "ui_discard_plaud_off",
        min_age_hours=cfg.min_age_minutes // 60, wait_hours=cfg.permanent_delete_after_hours,
    )
    context.update(t=t, lang=cfg.ui_lang, csrf=csrf_token(), plaud_note=plaud_note)
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def require_csrf(request: Request, token: str) -> None:
    """Reject state changes without our token or from another origin."""
    if not hmac.compare_digest(token or "", csrf_token()):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site")
    # Some browsers send `Origin: null` for same-origin form posts (depends on the referrer policy). That is
    # trusted only when the browser itself says the request is same-origin: pages cannot set Sec-Fetch-Site.
    null_from_same_origin = origin == "null" and fetch_site == "same-origin"
    if origin and not null_from_same_origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
        raise HTTPException(status_code=403, detail="origin mismatch")
    if fetch_site in ("cross-site", "same-site"):
        raise HTTPException(status_code=403, detail="cross-site request")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = Query("", max_length=200), cursor: str = Query("", max_length=200),
          session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    try:
        page = svc.list_recordings(session, now_utc(), _stability(cfg), q, cursor or None)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid cursor")
    return render(request, cfg, "list.html", page=page, q=q)


@app.get("/recordings/{recording_id}", response_class=HTMLResponse)
def detail(request: Request, recording_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], session: Session = Depends(get_session),
           cfg: Config = Depends(get_config)):
    found = svc.get_detail(session, recording_id)
    if found is None:
        raise HTTPException(status_code=404, detail="recording not found")
    rec, segments = found
    state = svc.recording_state(rec, now_utc(), _stability(cfg))
    return render(request, cfg, "detail.html", rec=rec, segments=segments, state=state)


@app.post("/recordings/{recording_id}/unanalyze")
def recording_unanalyze(request: Request, recording_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], csrf: str = Form(""),
                        session: Session = Depends(get_session)):
    """Let the routine read the recording again."""
    require_csrf(request, csrf)
    try:
        todos.unmark_analyzed(session, recording_id)
    except todos.TodoError:
        raise HTTPException(status_code=404, detail="recording not found")
    return RedirectResponse(f"/recordings/{recording_id}", status_code=303)


@app.get("/recordings/{recording_id}/discard", response_class=HTMLResponse)
def discard_confirm(request: Request, recording_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], session: Session = Depends(get_session),
                    cfg: Config = Depends(get_config)):
    found = svc.get_detail(session, recording_id)
    if found is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return render(request, cfg, "confirm_discard.html", rec=found[0])


@app.post("/recordings/{recording_id}/discard")
def discard_one(request: Request, recording_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], csrf: str = Form(""),
                session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    if not svc.discard(session, recording_id, now_utc()):
        raise HTTPException(status_code=404, detail="recording not found")
    return RedirectResponse("/", status_code=303)


@app.get("/cleanup", response_class=HTMLResponse)
def cleanup_preview(request: Request, days: int = Query(90, ge=1, le=3650),
                    session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    ids = svc.older_than_ids(session, days, now_utc())
    return render(request, cfg, "cleanup.html", days=days, count=len(ids), fingerprint=svc.ids_fingerprint(ids),
                  changed=False)


@app.post("/cleanup")
def cleanup_run(request: Request, days: int = Form(..., ge=1, le=3650), expected_count: int = Form(...),
                fingerprint: str = Form(..., max_length=32), csrf: str = Form(""),
                session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    require_csrf(request, csrf)
    now = now_utc()
    ids = svc.older_than_ids(session, days, now)
    if len(ids) != expected_count or not hmac.compare_digest(svc.ids_fingerprint(ids), fingerprint):
        # The set changed since the owner looked at it (not just its size): show it again, discard nothing.
        return render(request, cfg, "cleanup.html", status_code=409, days=days, count=len(ids),
                      fingerprint=svc.ids_fingerprint(ids), changed=True)
    svc.discard_ids(session, ids, now)
    return RedirectResponse("/", status_code=303)


def _stability(cfg: Config) -> int:
    return cfg.stability_minutes


# --- tasks, topics and overviews (REQ-006, D-013) -------------------------------------------
def _todo_error(exc: todos.TodoError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/todos", response_class=HTMLResponse)
def todo_list(request: Request, status: str = Query("open", max_length=10), topic: str = Query("", max_length=120),
              sort: str = Query("priority", max_length=10), session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    if status not in todos.STATUSES or sort not in todos.SORTS:
        raise HTTPException(status_code=400, detail="invalid status or sort")
    rows, truncated = todos.list_todos(session, now_utc(), status=status, topic=topic or None,
                                       limit=todos.LIST_LIMIT_MAX, sort=sort)
    return render(request, cfg, "todos.html", rows=rows, truncated=truncated, status=status, topic=topic, sort=sort, sorts=todos.SORTS,
                  today=now_utc().astimezone(LOCAL_TZ).date(),
                  counts=todos.counts(session), statuses=todos.STATUSES, priorities=todos.PRIORITIES)


@app.post("/todos")
def todo_add(request: Request, title: str = Form("", max_length=todos.TITLE_MAX), priority: int = Form(3),
             due: str = Form("", max_length=10), topic: str = Form("", max_length=todos.TOPIC_MAX),
             csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        todo = todos.create_todo(session, now_utc(), title=title, actor="owner", priority=priority,
                                 due=due or None, topic=topic or None, allow_similar=True)
    except todos.TodoError as exc:
        raise _todo_error(exc)
    return RedirectResponse(f"/todos/{todo.id}", status_code=303)


@app.get("/todos/{todo_id}", response_class=HTMLResponse)
def todo_detail(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], session: Session = Depends(get_session),
                cfg: Config = Depends(get_config)):
    found = todos.get_todo(session, now_utc(), todo_id)
    if found is None:
        raise HTTPException(status_code=404, detail="task not found")
    row, events = found
    undoable = events[0].id if events and events[0].kind in todos.UNDOABLE_KINDS else None
    return render(request, cfg, "todo.html", row=row, events=events, undoable=undoable,
                  statuses=todos.STATUSES, priorities=todos.PRIORITIES)


@app.post("/todos/{todo_id}")
def todo_edit(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], title: str = Form("", max_length=todos.TITLE_MAX),
              detail: str = Form("", max_length=todos.DETAIL_MAX), priority: int = Form(3),
              due: str = Form("", max_length=10), topic: str = Form("", max_length=todos.TOPIC_MAX),
              status: str = Form("open", max_length=10), csrf: str = Form(""),
              back: str = Form("", max_length=20), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        todos.update_todo(session, now_utc(), todo_id, actor="owner", title=title, detail=detail,
                          priority=priority, due=due, topic=topic, status=status)
    except todos.TodoError as exc:
        if "no task" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise _todo_error(exc)
    return RedirectResponse("/todos" if back == "list" else f"/todos/{todo_id}", status_code=303)


@app.post("/todos/{todo_id}/status")
def todo_status(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], status: str = Form("", max_length=10), csrf: str = Form(""),
                back: str = Form("", max_length=20), session: Session = Depends(get_session)):
    """One-click check-off from the list."""
    require_csrf(request, csrf)
    try:
        todos.update_todo(session, now_utc(), todo_id, actor="owner", status=status)
    except todos.TodoError as exc:
        if "no task" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise _todo_error(exc)
    return RedirectResponse("/todos" if back == "list" else f"/todos/{todo_id}", status_code=303)


@app.post("/todos/events/{event_id}/undo")
def todo_undo(request: Request, event_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        todo = todos.undo_event(session, now_utc(), event_id, actor="owner")
    except todos.TodoError as exc:
        raise _todo_error(exc)
    return RedirectResponse(f"/todos/{todo.id}", status_code=303)


@app.get("/topics", response_class=HTMLResponse)
def topic_list(request: Request, session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    return render(request, cfg, "topics.html", topics=todos.list_topics(session))


@app.post("/topics/exclude")
def topic_exclude(request: Request, name: str = Form("", max_length=todos.TOPIC_MAX), excluded: str = Form("1"),
                  csrf: str = Form(""), back: str = Form("", max_length=20),
                  session: Session = Depends(get_session)):
    """Owner decision: no new tasks or notes for this topic (also for topics that do not exist yet)."""
    require_csrf(request, csrf)
    try:
        topic = todos.set_topic_excluded(session, name, excluded == "1")
    except todos.TodoError as exc:
        raise _todo_error(exc)
    return RedirectResponse(f"/topics/{topic.id}" if back == "topic" else "/topics", status_code=303)


@app.post("/topics/{topic_id}/rename")
def topic_rename(request: Request, topic_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], name: str = Form("", max_length=todos.TOPIC_MAX),
                 csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        todos.rename_topic(session, topic_id, name)
    except todos.TodoError as exc:
        if "no topic" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise _todo_error(exc)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.get("/topics/{topic_id}/delete", response_class=HTMLResponse)
def topic_delete_confirm(request: Request, topic_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], session: Session = Depends(get_session),
                         cfg: Config = Depends(get_config)):
    found = topic_admin.topic_counts(session, topic_id)
    if found is None:
        raise HTTPException(status_code=404, detail="topic not found")
    topic, open_tasks, tasks, notes = found
    return render(request, cfg, "confirm_delete_topic.html", topic=topic, open_tasks=open_tasks, tasks=tasks,
                  notes=notes)


@app.post("/topics/delete")
def topic_delete(request: Request, topic_id: int = Form(..., ge=1, le=MAX_ID), expected_notes: int = Form(..., ge=0),
                 expected_tasks: int = Form(..., ge=0), exclude: str = Form(""),
                 csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    found = topic_admin.topic_counts(session, topic_id)
    if found is None:
        raise HTTPException(status_code=404, detail="topic not found")
    if (found[3], found[2]) != (expected_notes, expected_tasks):
        # Something was added since the owner looked at the page: nothing is deleted.
        raise HTTPException(status_code=409, detail=get_text("ui_changed_since", cfg_lang()))
    topic_admin.delete_topic(session, now_utc(), topic_id, keep_excluded=exclude == "1")
    return RedirectResponse("/topics", status_code=303)


@app.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_detail(request: Request, topic_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], session: Session = Depends(get_session),
                 cfg: Config = Depends(get_config)):
    found = todos.topic_timeline_by_id(session, topic_id)
    if found is None:
        raise HTTPException(status_code=404, detail="topic not found")
    topic, entries = found
    rows, _ = todos.list_todos(session, now_utc(), status="open", topic=topic.name, limit=todos.LIST_LIMIT_MAX,
                               include_excluded=True)
    return render(request, cfg, "topic.html", topic=topic, entries=entries, rows=rows)


@app.get("/digests", response_class=HTMLResponse)
@app.get("/digests/{day}", response_class=HTMLResponse)
def digest_page(request: Request, day: str = "", session: Session = Depends(get_session),
                cfg: Config = Depends(get_config)):
    """One day's overview with links to the previous and next saved day; the latest by default."""
    recent = todos.list_digests(session, 14)
    if not day:
        digest = recent[0] if recent else None
    else:
        try:
            digest = todos.get_digest(session, day)
        except todos.TodoError:
            raise HTTPException(status_code=404, detail="overview not found")
        if digest is None:
            raise HTTPException(status_code=404, detail="overview not found")
    older, newer = todos.digest_neighbours(session, digest.day) if digest else (None, None)
    day_recordings = todos.recordings_of_day(session, digest.day) if digest else []
    versions = todos.digest_versions(session, digest.day) if digest else []
    return render(request, cfg, "digests.html", digest=digest, recent=recent, older=older, newer=newer,
                  day_recordings=day_recordings, versions=versions)
