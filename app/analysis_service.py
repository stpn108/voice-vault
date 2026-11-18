"""Server-side analysis of new recordings (REQ-007, D-072).

Picks the recordings to handle, runs the routine of REQ-006 as a tool loop and records every run and request.
The model reaches the database only through mcp_tools.call_tool, the function the MCP server uses, so every
rule and guard of REQ-006 applies unchanged.
"""
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

import mcp_tools
import recording_service
from database import AnalysisRun, LlmTrace, Recording
from llm_client import SYSTEM_PROMPT, LlmError, Turn, api_tools, wrap_untrusted
from mcp_args import text_result
from utils import LOCAL_TZ, as_utc

log = logging.getLogger(__name__)

CALL_TYPE = "analysis"
MAX_ATTEMPTS = 2  # runs within RETRY_WINDOW that did not get a recording marked analyzed
RETRY_WINDOW = dt.timedelta(hours=24)


@dataclass(frozen=True)
class Settings:
    stability_minutes: int
    lookback_days: int
    max_recordings: int
    max_requests: int


@dataclass
class RunResult:
    run_id: int
    status: str  # ok, failed, limit
    recording_ids: list
    done_ids: list
    requests: int
    tool_calls: int
    error: str = ""


def _ids(text: str) -> list:
    return [int(v) for v in text.split(",") if v]


def _csv(ids) -> str:
    return ",".join(str(i) for i in ids)


def _failed_attempts(session: Session, now: dt.datetime) -> dict:
    """Recording id -> number of recent runs that had it and did not get it marked analyzed."""
    attempts = {}
    for run in session.scalars(select(AnalysisRun).where(AnalysisRun.started_at >= now - RETRY_WINDOW)):
        done = set(_ids(run.done_ids))
        for rec_id in _ids(run.recording_ids):
            if rec_id not in done:
                attempts[rec_id] = attempts.get(rec_id, 0) + 1
    return attempts


def pick_recordings(session: Session, now: dt.datetime, settings: Settings) -> list:
    """The recordings a run handles, oldest first (criteria 2 and 8)."""
    since = now - dt.timedelta(days=settings.lookback_days)
    rows, cursor = [], None
    while True:
        page = recording_service.list_recordings(session, now, settings.stability_minutes, cursor=cursor,
                                                 since=since, unanalyzed_only=True)
        rows += page.rows
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    attempts = _failed_attempts(session, now)
    ready = [r for r in reversed(rows)  # list_recordings is newest first
             if r.state != recording_service.STATE_WAITING and attempts.get(r.id, 0) < MAX_ATTEMPTS]
    skipped = [r.id for r in rows if attempts.get(r.id, 0) >= MAX_ATTEMPTS]
    if skipped:
        log.info("Analysis skips recordings that failed %d times within 24 h ids=%s", MAX_ATTEMPTS, skipped)
    return [r.id for r in ready[:settings.max_recordings]]


def first_message(session: Session, recording_ids: list, now: dt.datetime) -> str:
    """Ids, start time and length only: titles are recording content and are read through the tools."""
    lines = [f"Today is {now.astimezone(LOCAL_TZ):%Y-%m-%d %H:%M} (Europe/Berlin). Recordings to handle, oldest first:"]
    for rec_id in recording_ids:
        rec = session.get(Recording, rec_id)
        lines.append(f"- id {rec.id}, started {as_utc(rec.started_at).astimezone(LOCAL_TZ):%Y-%m-%d %H:%M}, "
                     f"{(rec.duration_ms or 0) // 60000} min")
    return "\n".join(lines)


def _execute(session_factory, call, now_fn, stability_minutes) -> dict:
    with session_factory() as session:
        try:
            result = mcp_tools.call_tool(session, call.name, call.input, now_fn(), stability_minutes)
        except mcp_tools.ToolArgumentError as exc:
            result = text_result(str(exc), is_error=True)
        except Exception:  # noqa: BLE001 - like the MCP server: the model gets an error, the log the traceback
            log.exception("Analysis tool call failed tool=%s", call.name)
            result = text_result("internal error", is_error=True)
    log.info("Analysis tool call tool=%s is_error=%s", call.name, result["isError"])
    text = "\n".join(c["text"] for c in result["content"])
    return {"type": "tool_result", "tool_use_id": call.id, "content": wrap_untrusted(text),
            "is_error": result["isError"]}


def _trace(session_factory, run_id: int, now: dt.datetime, status: str, turn: Turn = None,
           error: LlmError = None) -> None:
    row = LlmTrace(ts_utc=now, call_type=CALL_TYPE, run_id=run_id, status=status)
    if turn is not None:
        row.model, row.stop_reason, row.latency_ms = turn.model, turn.stop_reason, turn.latency_ms
        row.input_tokens, row.output_tokens = turn.input_tokens, turn.output_tokens
        row.cache_read_tokens, row.cache_write_tokens = turn.cache_read_tokens, turn.cache_write_tokens
        row.request_id, row.request_hash, row.response_hash = turn.request_id, turn.request_hash, turn.response_hash
    if error is not None:
        row.latency_ms, row.request_hash = error.latency_ms, error.request_hash
    with session_factory() as session:
        session.add(row)
        session.commit()


def _loop(session_factory, llm, run_id: int, recording_ids: list, settings: Settings, now_fn) -> tuple:
    """(status, error, requests, tool_calls)."""
    tools = api_tools(mcp_tools.TOOLS)
    with session_factory() as session:
        messages = [{"role": "user", "content": first_message(session, recording_ids, now_fn())}]
    requests = tool_calls = 0
    while requests < settings.max_requests:
        requests += 1
        try:
            turn = llm.turn(SYSTEM_PROMPT, messages, tools)
        except LlmError as exc:
            _trace(session_factory, run_id, now_fn(), exc.status, error=exc)
            return "failed", f"{exc.status}: {exc.reason}", requests, tool_calls
        if turn.stop_reason == "refusal":
            _trace(session_factory, run_id, now_fn(), "refusal", turn)
            return "failed", "refusal", requests, tool_calls
        _trace(session_factory, run_id, now_fn(), "ok", turn)
        messages.append({"role": "assistant", "content": turn.content})
        if not turn.tool_calls:
            if turn.stop_reason == "end_turn":
                return "ok", "", requests, tool_calls
            return "failed", f"stopped: {turn.stop_reason}", requests, tool_calls
        results = [_execute(session_factory, call, now_fn, settings.stability_minutes) for call in turn.tool_calls]
        tool_calls += len(results)
        messages.append({"role": "user", "content": results})  # all results of one turn in one message
    return "limit", f"stopped after {settings.max_requests} requests", requests, tool_calls


def run_analysis(session_factory: Callable[[], Session], llm, now_fn: Callable[[], dt.datetime],
                 settings: Settings):
    """One run. Returns a RunResult, or None when there was nothing to do (no API call then)."""
    now = now_fn()
    with session_factory() as session:
        recording_ids = pick_recordings(session, now, settings)
        if not recording_ids:
            return None
        run = AnalysisRun(started_at=now, status="running", recording_ids=_csv(recording_ids))
        session.add(run)
        session.commit()
        run_id = run.id
    log.info("Analysis run start id=%s recordings=%s", run_id, recording_ids)
    status, error, requests, tool_calls = _loop(session_factory, llm, run_id, recording_ids, settings, now_fn)
    with session_factory() as session:
        done = session.scalars(select(Recording.id).where(Recording.id.in_(recording_ids),
                                                          Recording.analyzed_at.is_not(None))).all()
        done_ids = [i for i in recording_ids if i in set(done)]
        run = session.get(AnalysisRun, run_id)
        run.finished_at, run.status, run.error = now_fn(), status, error[:300]
        run.done_ids, run.requests, run.tool_calls = _csv(done_ids), requests, tool_calls
        session.commit()
    log_fn = log.info if status == "ok" else log.error
    log_fn("Analysis run end id=%s status=%s requests=%d tool_calls=%d analyzed=%s of %s error=%s",
           run_id, status, requests, tool_calls, done_ids, recording_ids, error or "-")
    return RunResult(run_id, status, recording_ids, done_ids, requests, tool_calls, error)
