"""
Web UI to view, search and discard stored recordings (REQ-002, D-008).

No login: the port is bound to 127.0.0.1 and reached through the owner's SSH tunnel.
Instead the app defends against a hostile web page in the owner's browser:
host header check (DNS rebinding), CSRF token plus Origin check on every state
change, escaped output and a strict Content-Security-Policy.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

import recording_service as svc
import todo_service as todos
import briefing_service
import people_admin
import person_service
import stats_service
import task_links
import topic_admin
from config import Config, load_config
from database import Topic, engine, migrate_schema
from markdown_lite import render_markdown
from strings import get_text
from utils import LOCAL_TZ, now_utc, setup_logging

setup_logging()
log = logging.getLogger(__name__)

# Per process unless UI_SECRET is set: forms from before a restart are rejected.
CSRF_SECRET = (os.getenv("UI_SECRET") or secrets.token_hex(32)).encode()
LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
# Scripts only from this server's own /static files (no inline code, D-027); fetch only to this server.
CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
STATIC_DIR = Path(__file__).resolve().parent / "static"
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
WIP_LIMIT = 20  # tasks shown in the "in progress" reminder of every page (D-028)


def load_in_progress(request: Request, session: Session = Depends(get_session)) -> None:
    """Every page shows the owner's tasks in progress as a reminder in the header."""
    if request.method == "GET":
        request.state.in_progress = _in_progress(session)
        request.state.wip_count = todos.counts(session)["in_progress"]


def _in_progress(session: Session) -> list:
    rows = todos.list_todos(session, now_utc(), status="in_progress", limit=WIP_LIMIT)[0]
    return [{"id": row.id, "title": row.title} for row in rows]


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
              dependencies=[Depends(load_in_progress)])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS + list(cfg_at_start.ui_allowed_hosts))
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
    immediate = cfg.min_age_minutes <= 60 and cfg.permanent_delete_after_hours == 0
    plaud_note = t(
        ("ui_discard_plaud_now" if immediate else "ui_discard_plaud_on") if cfg.delete_enabled else "ui_discard_plaud_off",
        min_age_hours=cfg.min_age_minutes // 60, wait_hours=cfg.permanent_delete_after_hours,
    )
    context.update(t=t, lang=cfg.ui_lang, csrf=csrf_token(), plaud_note=plaud_note,
                   in_progress=getattr(request.state, "in_progress", []),
                   wip_count=getattr(request.state, "wip_count", 0))
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
    return render(request, cfg, "list.html", page=page, q=q, sync=svc.sync_state(session, now_utc()),
                  last_sync=svc.last_sync_request(session))


@app.post("/sync")
def sync_now(request: Request, csrf: str = Form(""), session: Session = Depends(get_session)):
    """"Jetzt abholen": ask the app container for an import cycle now (D-041)."""
    require_csrf(request, csrf)
    svc.request_sync(session, now_utc())
    return RedirectResponse("/", status_code=303)


@app.get("/sync/status")
def sync_status(session: Session = Depends(get_session)):
    """Polled by /static/sync.js while a fetch runs (D-045)."""
    return JSONResponse(svc.sync_state(session, now_utc()))


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
def todo_list(request: Request, status: str = Query("open", max_length=12), topic: str = Query("", max_length=120),
              person: str = Query("", max_length=todos.PERSON_MAX), kind: str = Query("mine", max_length=10),
              sort: str = Query("priority", max_length=10), session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    if status not in todos.STATUSES or sort not in todos.SORTS or kind not in todos.KINDS:
        raise HTTPException(status_code=400, detail="invalid status, sort or kind")
    rows, truncated = todos.list_todos(session, now_utc(), status=status, topic=topic or None, person=person or None,
                                       kind=kind, limit=todos.LIST_LIMIT_MAX, sort=sort)
    # What others owe the owner is not started or put back, so that list has no "in progress" tab (D-038).
    statuses = [s for s in todos.STATUSES if kind == "mine" or s != "in_progress"]
    # "Wartet auf": who owes how much, as a bar of people to filter by (D-039).
    # Excluded people (D-044) are not shown in the list, neither in the bar nor as tags (D-055).
    hidden = todos.excluded_people(session)
    owed = Counter()
    if kind == "waiting":
        for row in todos.list_todos(session, now_utc(), kind="waiting", limit=todos.LIST_LIMIT_MAX)[0]:
            owed.update(n for n in row.people if n.lower() not in hidden)
    shown_people = {row.id: [n for n in row.people if n.lower() not in hidden] for row in rows}
    return render(request, cfg, "todos.html", rows=rows, truncated=truncated, status=status, topic=topic, person=person,
                  kind=kind, kinds=todos.KINDS, kind_counts={k: todos.counts(session, k)["open"] for k in todos.KINDS},
                  sort=sort, sorts=todos.SORTS, today=now_utc().astimezone(LOCAL_TZ).date(),
                  counts=todos.counts(session, kind), statuses=statuses, priorities=todos.PRIORITIES,
                  known_people=todos.known_people(session) if kind == "waiting" else [],
                  links=task_links.link_map(session, [r.id for r in rows]), shown_people=shown_people,
                  owed=sorted(owed.items(), key=lambda pair: (-pair[1], pair[0].lower())))


@app.post("/todos")
def todo_add(request: Request, title: str = Form("", max_length=todos.TITLE_MAX), priority: int = Form(3),
             due: str = Form("", max_length=10), topic: str = Form("", max_length=todos.TOPIC_MAX),
             kind: str = Form("mine", max_length=10), person: str = Form("", max_length=todos.PERSON_MAX),
             csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        todo = todos.create_todo(session, now_utc(), title=title, actor="owner", priority=priority,
                                 due=due or None, topic=topic or None, allow_similar=True, kind=kind,
                                 people=[person] if person.strip() else [])
    except todos.TodoError as exc:
        raise _todo_error(exc)
    return RedirectResponse(f"/todos/{todo.id}", status_code=303)


LINK_TARGET = re.compile(r"^\s*#?(\d{1,9})\b")  # "#12 Title" from the suggestion list, or a bare id


def _link_ids(a: int, target: str) -> tuple:
    found = LINK_TARGET.match(target or "")
    if not found:
        raise HTTPException(status_code=400, detail="pick a task from the list")
    return a, int(found.group(1))


@app.post("/todos/links")
def todo_link(request: Request, a: int = Form(..., ge=1, le=MAX_ID), b: str = Form(..., max_length=320),
              back: str = Form("", max_length=20), csrf: str = Form(""), session: Session = Depends(get_session)):
    """Link two tasks: a line drawn in the list (JSON) or a pick on the task page (D-047)."""
    require_csrf(request, csrf)
    first, second = _link_ids(a, b)
    try:
        created = task_links.link(session, now_utc(), first, second)
    except task_links.LinkError as exc:
        raise HTTPException(status_code=404 if "no task" in str(exc) else 400, detail=str(exc))
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "created": created})
    return RedirectResponse(f"/todos/{first}" if back == "detail" else "/todos/graph", status_code=303)


@app.post("/todos/links/remove")
def todo_unlink(request: Request, a: int = Form(..., ge=1, le=MAX_ID), b: str = Form(..., max_length=320),
                back: str = Form("", max_length=20), csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    first, second = _link_ids(a, b)
    try:
        removed = task_links.unlink(session, now_utc(), first, second)
    except task_links.LinkError as exc:
        raise HTTPException(status_code=404 if "no task" in str(exc) else 400, detail=str(exc))
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "removed": removed})
    return RedirectResponse(f"/todos/{first}" if back == "detail" else "/todos/graph", status_code=303)


@app.post("/todos/graph/layout")
def todo_graph_layout(request: Request, layout: str = Form(..., max_length=200_000), csrf: str = Form(""),
                      session: Session = Depends(get_session)):
    """Remember where the owner put Mindmap boxes and how big he made them (D-054)."""
    require_csrf(request, csrf)
    try:
        stored = task_links.save_layout(session, now_utc(), json.loads(layout))
    except (ValueError, task_links.LinkError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid layout: {exc}")
    return JSONResponse({"ok": True, "stored": stored})


@app.get("/todos/graph", response_class=HTMLResponse)
def todo_graph(request: Request, done: int = Query(0, ge=0, le=1), topics: int = Query(0, ge=0, le=1),
               everything: int = Query(0, ge=0, le=1, alias="all"), waiting: int = Query(0, ge=0, le=1),
               session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    """The tasks and their links as a graph (D-047)."""
    data = task_links.graph(session, done=bool(done), topics=bool(topics), everything=bool(everything),
                            waiting=bool(waiting))
    return render(request, cfg, "graph.html", data=data, done=done, topics=topics, everything=everything,
                  waiting=waiting)


@app.get("/todos/{todo_id}", response_class=HTMLResponse)
def todo_detail(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], session: Session = Depends(get_session),
                person: str = Query("", max_length=todos.PERSON_MAX), cfg: Config = Depends(get_config)):
    found = todos.get_todo(session, now_utc(), todo_id)
    if found is None:
        raise HTTPException(status_code=404, detail="task not found")
    row, events = found
    undoable = events[0].id if events and events[0].kind in todos.UNDOABLE_KINDS else None
    speakers = todos.speakers_of(session, row.recording_id)
    # The speakers of the task's recording are offered as checkboxes; the people already named stay ticked.
    candidates = row.people + [s for s in speakers if s.lower() not in {p.lower() for p in row.people}]
    # Topics as tags (D-036): the task's topic first, then the busiest topics the routine may still use.
    allowed = [t.name for t, *_ in todos.list_topics(session) if not t.excluded]
    topic_candidates = ([row.topic] if row.topic else []) + [n for n in allowed if n.lower() != (row.topic or "").lower()]
    # Opened from a person's briefing or a person filter: the breadcrumb leads back there (D-040).
    via_person = next((p for p in row.people if p.lower() == " ".join(person.split()).lower()), None)
    linked = task_links.linked_to(session, row.id)
    targets = [r for r in todos.list_todos(session, now_utc(), status="open", kind="all", limit=todos.LIST_LIMIT_MAX)[0]
               if r.id != row.id and r.id not in {item[0] for item in linked}]
    return render(request, cfg, "todo.html", row=row, events=events, undoable=undoable, candidates=candidates, via_person=via_person,
                  linked=linked, link_targets=targets,
                  topic_candidates=topic_candidates[:TOPIC_TAGS], known_topics=allowed,
                  speakers=speakers, known=todos.known_people(session), statuses=todos.STATUSES, priorities=todos.PRIORITIES)


@app.post("/todos/{todo_id}")
def todo_edit(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], title: str = Form("", max_length=todos.TITLE_MAX),
              detail: str = Form("", max_length=todos.DETAIL_MAX), priority: int = Form(3),
              due: str = Form("", max_length=10), topic: str = Form("", max_length=todos.TOPIC_MAX),
              status: str = Form("open", max_length=12), people: list[str] = Form([]),
              people_more: str = Form("", max_length=400), topic_more: str = Form("", max_length=todos.TOPIC_MAX),
              csrf: str = Form(""), back: str = Form("", max_length=20), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    names = people + people_more.split(",")
    topic = topic_more.strip() or topic
    try:
        todos.update_todo(session, now_utc(), todo_id, actor="owner", title=title, detail=detail,
                          priority=priority, due=due, topic=topic, status=status, people=names)
    except todos.TodoError as exc:
        if "no task" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise _todo_error(exc)
    if "application/json" in request.headers.get("accept", ""):
        # Saved in the background from the task page (D-032); the header reminder follows the status.
        return JSONResponse({"ok": True, "id": todo_id, "status": status, "wip": todos.counts(session)["in_progress"],
                             "counts": todos.counts(session, todos.get_todo(session, now_utc(), todo_id)[0].kind),
                             "in_progress": _in_progress(session), "detail_html": str(render_markdown(detail.strip()))})
    return RedirectResponse("/todos" if back == "list" else f"/todos/{todo_id}", status_code=303)


TOPIC_TAGS = 6  # topic tags offered on the task page; every other topic is suggested in the text field


@app.post("/todos/{todo_id}/topic")
def todo_topic(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)],
               topic: str = Form("", max_length=todos.TOPIC_MAX), csrf: str = Form(""),
               session: Session = Depends(get_session)):
    """Topic tag on the task page: a tap sets, replaces or (with nothing ticked) clears the topic (D-036)."""
    require_csrf(request, csrf)
    try:
        todos.update_todo(session, now_utc(), todo_id, actor="owner", topic=topic)
    except todos.TodoError as exc:
        if "no task" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise _todo_error(exc)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "topic": todos.get_todo(session, now_utc(), todo_id)[0].topic})
    return RedirectResponse(f"/todos/{todo_id}", status_code=303)


@app.post("/todos/{todo_id}/people")
def todo_people(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], people: list[str] = Form([]),
                csrf: str = Form(""), session: Session = Depends(get_session)):
    """Tags on the task page: every tap saves the whole list at once (D-031)."""
    require_csrf(request, csrf)
    try:
        todos.update_todo(session, now_utc(), todo_id, actor="owner", people=people)
    except todos.TodoError as exc:
        if "no task" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise _todo_error(exc)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "people": todos.get_todo(session, now_utc(), todo_id)[0].people})
    return RedirectResponse(f"/todos/{todo_id}", status_code=303)


@app.post("/todos/{todo_id}/status")
def todo_status(request: Request, todo_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], status: str = Form("", max_length=12), csrf: str = Form(""),
                back: str = Form("", max_length=20), session: Session = Depends(get_session)):
    """One-click check-off from the list. The page script asks for JSON and updates the list in place."""
    require_csrf(request, csrf)
    found = todos.get_todo(session, now_utc(), todo_id)
    previous = found[0].status if found else None
    try:
        todos.update_todo(session, now_utc(), todo_id, actor="owner", status=status)
    except todos.TodoError as exc:
        if "no task" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise _todo_error(exc)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "id": todo_id, "status": status, "previous": previous,
                             "counts": todos.counts(session, found[0].kind), "wip": todos.counts(session)["in_progress"],
                             "in_progress": _in_progress(session)})
    if back == "page" and request.headers.get("referer", "").startswith(f"{request.url.scheme}://{request.headers.get('host')}/"):
        return RedirectResponse(request.headers["referer"], status_code=303)
    if back == "list":
        return RedirectResponse("/todos?kind=waiting" if found and found[0].kind == "waiting" else "/todos", status_code=303)
    return RedirectResponse(f"/todos/{todo_id}", status_code=303)


@app.post("/todos/events/{event_id}/undo")
def todo_undo(request: Request, event_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        todo = todos.undo_event(session, now_utc(), event_id, actor="owner")
    except todos.TodoError as exc:
        raise _todo_error(exc)
    return RedirectResponse(f"/todos/{todo.id}", status_code=303)


@app.get("/person", response_class=HTMLResponse)
def person_page(request: Request, name: str = Query(..., max_length=todos.PERSON_MAX), session: Session = Depends(get_session),
                cfg: Config = Depends(get_config)):
    """Briefing before talking to someone (D-038)."""
    found = briefing_service.briefing(session, now_utc(), name)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown person")
    excluded = found.name.lower() in todos.excluded_people(session)
    return render(request, cfg, "person.html", b=found, today=now_utc().astimezone(LOCAL_TZ).date(),
                  days_since=(now_utc() - found.last_talk).days if found.last_talk else None, excluded=excluded,
                  profile=None if excluded else person_service.profile(session, found.name),
                  clusters=[] if excluded else person_service.clusters(session, found.name))


@app.post("/person/profile")
def person_profile_save(request: Request, name: str = Form(..., max_length=todos.PERSON_MAX),
                        field: str = Form(..., max_length=20), value: str = Form("", max_length=2000),
                        csrf: str = Form(""), session: Session = Depends(get_session)):
    """One field of the living profile, saved when the owner leaves it (D-057). `field` is a profile field
    or "topic:<id>" for the note on a shared topic."""
    require_csrf(request, csrf)
    try:
        if field in ("role", "current_work", "next_points"):
            person_service.update_profile(session, now_utc(), name, actor="owner", **{field: value})
        elif field.startswith("topic:") and field[6:].isdigit():
            topic = session.get(Topic, int(field[6:]))
            if topic is None:
                raise HTTPException(status_code=404, detail="unknown topic")
            person_service.set_topic_note(session, now_utc(), name, topic.name, value, actor="owner")
        else:
            raise HTTPException(status_code=400, detail="unknown field")
    except (person_service.PersonError, todos.TodoError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse({"ok": True})


@app.get("/person/graph", response_class=HTMLResponse)
def person_graph_page(request: Request, name: str = Query(..., max_length=todos.PERSON_MAX),
                      session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    """The person's own Mindmap: their topics, tasks and promises, and topics that probably belong together (D-058)."""
    data = person_service.person_graph(session, now_utc(), name)
    if data is None or data["person"].lower() in todos.excluded_people(session):
        raise HTTPException(status_code=404, detail="unknown person")
    return render(request, cfg, "graph.html", data=data, person=data["person"], done=0, topics=1, everything=0, waiting=1)


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    return render(request, cfg, "stats.html", data=stats_service.figures(session, now_utc()),
                  priorities=todos.PRIORITIES)


# --- administration (D-034) -------------------------------------------------------------------
# Every admin tool is one entry here and gets a tile on /admin. A new tool: add an entry, its routes
# under /admin/..., its template and strings.
ADMIN_TOOLS = [
    {"path": "/admin/people", "title": "ui_admin_people", "text": "ui_admin_people_text"},
    {"path": "/cleanup", "title": "ui_cleanup", "text": "ui_admin_cleanup_text"},
]
PEOPLE_MESSAGES = ("renamed", "deleted", "assigned", "excluded", "allowed")


def _people_error(exc: people_admin.PeopleError) -> HTTPException:
    return HTTPException(status_code=404 if "unknown" in str(exc) or "no recording" in str(exc) else 400,
                         detail=str(exc))


@app.get("/admin", response_class=HTMLResponse)
def admin_index(request: Request, cfg: Config = Depends(get_config)):
    return render(request, cfg, "admin.html", tools=ADMIN_TOOLS)


@app.get("/admin/people", response_class=HTMLResponse)
def admin_people(request: Request, done: str = Query("", max_length=10), session: Session = Depends(get_session),
                 cfg: Config = Depends(get_config)):
    return render(request, cfg, "people.html", people=people_admin.people(session), orphans=people_admin.orphans(session),
                  known=todos.known_people(session), done=done if done in PEOPLE_MESSAGES else "")


@app.post("/admin/people/rename")
def admin_people_rename(request: Request, old: str = Form(..., max_length=todos.PERSON_MAX),
                        new: str = Form(..., max_length=todos.PERSON_MAX), csrf: str = Form(""),
                        session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        people_admin.rename_person(session, now_utc(), old, new)
    except (people_admin.PeopleError, todos.TodoError) as exc:
        raise _people_error(exc) if isinstance(exc, people_admin.PeopleError) else _todo_error(exc)
    return RedirectResponse("/admin/people?done=renamed", status_code=303)


@app.post("/admin/people/exclude")
def admin_people_exclude(request: Request, name: str = Form(..., max_length=todos.PERSON_MAX), excluded: str = Form("1"),
                         csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        people_admin.set_person_excluded(session, name, excluded == "1")
    except people_admin.PeopleError as exc:
        raise _people_error(exc)
    return RedirectResponse("/admin/people?done=" + ("excluded" if excluded == "1" else "allowed"), status_code=303)


@app.get("/admin/people/delete", response_class=HTMLResponse)
def admin_people_delete_confirm(request: Request, name: str = Query(..., max_length=todos.PERSON_MAX),
                                session: Session = Depends(get_session), cfg: Config = Depends(get_config)):
    found = people_admin.person(session, name)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown name")
    return render(request, cfg, "confirm_delete_person.html", person=found)


@app.post("/admin/people/delete")
def admin_people_delete(request: Request, name: str = Form(..., max_length=todos.PERSON_MAX),
                        expected_tasks: int = Form(..., ge=0), expected_segments: int = Form(..., ge=0),
                        csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    found = people_admin.person(session, name)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown name")
    if (found.tasks, found.segments) != (expected_tasks, expected_segments):
        raise HTTPException(status_code=409, detail=get_text("ui_changed_since", cfg_lang()))
    people_admin.delete_person(session, now_utc(), found.name)
    return RedirectResponse("/admin/people?done=deleted", status_code=303)


@app.post("/admin/people/assign")
def admin_people_assign(request: Request, recording_id: int = Form(..., ge=1, le=MAX_ID),
                        label: str = Form("", max_length=255), name: str = Form(..., max_length=todos.PERSON_MAX),
                        csrf: str = Form(""), session: Session = Depends(get_session)):
    require_csrf(request, csrf)
    try:
        people_admin.assign_speaker(session, recording_id, label, name)
    except people_admin.PeopleError as exc:
        raise _people_error(exc)
    return RedirectResponse("/admin/people?done=assigned", status_code=303)


TOPIC_SORTS = ("activity", "name", "open")


@app.get("/topics", response_class=HTMLResponse)
def topic_list(request: Request, sort: str = Query("activity", max_length=10), session: Session = Depends(get_session),
               cfg: Config = Depends(get_config)):
    if sort not in TOPIC_SORTS:
        raise HTTPException(status_code=400, detail="invalid sort")
    topics = todos.list_topics(session)
    if sort == "activity":
        topics.sort(key=lambda row: row[3], reverse=True)
    elif sort == "name":
        topics.sort(key=lambda row: row[0].name.lower())
    return render(request, cfg, "topics.html", topics=topics, sort=sort, sorts=TOPIC_SORTS,
                  allowed=[row[0].name for row in sorted(topics, key=lambda row: row[0].name.lower()) if not row[0].excluded])


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
