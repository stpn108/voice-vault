"""MCP server (REQ-003, D-011): authentication, protocol handshake and the read-only tools."""
import dataclasses
import datetime as dt
import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mcp_server
import mcp_tools
from config import load_config
from database import Recording, Segment
from tests.helpers import add_recording

TOKEN = "t" * 40
OTHER_TOKEN = "o" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_cfg(**overrides):
    base = dict(mcp_tokens=(TOKEN,), mcp_allowed_hosts=("mcp.example.org",),
                mcp_allowed_origins=("https://allowed.example",))
    base.update(overrides)
    return dataclasses.replace(load_config(), **base)


@pytest.fixture
def client(session_factory):
    app = mcp_server.create_app(make_cfg(), session_factory)
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def seed(session_factory):
    def add(**kwargs):
        with session_factory() as s:
            return add_recording(s, **kwargs).id
    return add


def rpc(client, method, params=None, id=1, headers=AUTH, **kwargs):
    message = {"jsonrpc": "2.0", "method": method, "id": id}
    if params is not None:
        message["params"] = params
    return client.post("/mcp", json=message, headers=headers, **kwargs)


def call(client, name, arguments=None):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})


def text_of(response):
    return response.json()["result"]["content"][0]["text"]


# --- start-up safety ---------------------------------------------------------
@pytest.mark.parametrize("tokens", [(), ("short",), ("x" * 31,), (TOKEN, "short")])
def test_req_003_server_refuses_to_start_without_a_strong_token(session_factory, tokens):
    with pytest.raises(mcp_server.ConfigError):
        mcp_server.create_app(make_cfg(mcp_tokens=tokens), session_factory)


def test_strong_tokens_are_accepted():
    assert mcp_server.validate_tokens((TOKEN, OTHER_TOKEN)) == (TOKEN, OTHER_TOKEN)


# --- authentication ------------------------------------------------------------
@pytest.mark.parametrize("headers", [
    {}, {"Authorization": ""}, {"Authorization": "Bearer"}, {"Authorization": "Bearer wrong" + "x" * 30},
    {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}, {"Authorization": f"Bearer {TOKEN}x"},
    {"Authorization": f"Bearer {TOKEN[:-1]}"}, {"x-api-key": TOKEN},
])
def test_req_003_requests_without_the_exact_token_are_rejected(client, headers):
    response = rpc(client, "ping", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert "result" not in response.text


def test_rejected_request_never_touches_the_database():
    def explode():
        raise AssertionError("the database must not be used before authentication")
    client = TestClient(mcp_server.create_app(make_cfg(), explode), base_url="http://localhost")
    bad = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "list_recordings"}})
    assert bad.status_code == 401


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_scheme_is_case_insensitive(client, scheme):
    assert rpc(client, "ping", headers={"Authorization": f"{scheme} {TOKEN}"}).status_code == 200


def test_both_tokens_work_during_a_rotation(session_factory):
    app = mcp_server.create_app(make_cfg(mcp_tokens=(TOKEN, OTHER_TOKEN)), session_factory)
    client = TestClient(app, base_url="http://localhost")
    for token in (TOKEN, OTHER_TOKEN):
        assert rpc(client, "ping", headers={"Authorization": f"Bearer {token}"}).status_code == 200


@pytest.mark.parametrize("presented,expected", [
    (TOKEN, True), (OTHER_TOKEN, True), ("", False), (TOKEN + "x", False), ("t" * 39, False),
])
def test_token_is_valid(presented, expected):
    assert mcp_server.token_is_valid(presented, (TOKEN, OTHER_TOKEN)) is expected


def test_failed_login_is_logged_without_the_token(client, caplog):
    with caplog.at_level(logging.WARNING):
        rpc(client, "ping", headers={"Authorization": "Bearer " + "z" * 40, "X-Forwarded-For": "203.0.113.9"})
    assert "bad or missing token" in caplog.text and "203.0.113.9" in caplog.text
    assert "z" * 40 not in caplog.text


# --- host and origin -------------------------------------------------------------
@pytest.mark.parametrize("host,status", [
    ("localhost", 200), ("127.0.0.1:8000", 200), ("mcp.example.org", 200),
    ("evil.example.com", 400), ("mcp.example.org.evil.com", 400),
])
def test_req_003_host_header_is_checked(session_factory, host, status):
    app = mcp_server.create_app(make_cfg(), session_factory)
    client = TestClient(app, base_url="http://localhost")
    assert rpc(client, "ping", headers={**AUTH, "host": host}).status_code == status


@pytest.mark.parametrize("origin,status", [
    (None, 200), ("https://allowed.example", 200), ("https://evil.example", 403), ("null", 403),
])
def test_req_003_origin_must_be_absent_or_allowed(client, origin, status):
    headers = {**AUTH, **({"Origin": origin} if origin else {})}
    assert rpc(client, "ping", headers=headers).status_code == status


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_other_methods_on_the_endpoint_are_405(client, method):
    response = client.request(method, "/mcp", headers=AUTH)
    assert response.status_code == 405 and response.headers["allow"] == "POST"


def test_healthz_needs_no_token_and_reveals_nothing(client):
    response = client.get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_healthz_is_503_when_the_database_is_down():
    def broken():
        raise RuntimeError("connection refused to db:5432 as user secret")
    client = TestClient(mcp_server.create_app(make_cfg(), broken), base_url="http://localhost")
    response = client.get("/healthz")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


@pytest.mark.parametrize("path", ["/", "/docs", "/openapi.json", "/redoc", "/admin"])
def test_nothing_else_is_served(client, path):
    assert client.get(path, headers=AUTH).status_code == 404


def test_response_headers_forbid_caching(client):
    response = rpc(client, "ping")
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"


# --- transport -------------------------------------------------------------------
def test_oversized_body_is_413(client):
    big = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * (mcp_server.MAX_BODY_BYTES + 1)}}
    assert client.post("/mcp", json=big, headers=AUTH).status_code == 413


@pytest.mark.parametrize("body", [b"not json", b"{", b""])
def test_invalid_json_is_a_parse_error(client, body):
    response = client.post("/mcp", content=body, headers=AUTH)
    assert response.status_code == 400 and response.json()["error"]["code"] == -32700


@pytest.mark.parametrize("message", [[], 5, "text", {"id": 1, "method": "ping"}, {"jsonrpc": "1.0", "id": 1, "method": "ping"}])
def test_invalid_requests(client, message):
    response = client.post("/mcp", json=message, headers=AUTH)
    assert response.status_code in (200, 400) and response.json()["error"]["code"] == -32600


def test_notifications_get_202_without_a_body(client):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=AUTH)
    assert response.status_code == 202 and response.content == b""


def test_client_responses_are_acknowledged_with_202(client):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 7, "result": {}}, headers=AUTH)
    assert response.status_code == 202


def test_batch_returns_one_response_per_request(client):
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    response = client.post("/mcp", json=batch, headers=AUTH)
    assert [r["id"] for r in response.json()] == [1, 2]


# --- handshake -------------------------------------------------------------------
@pytest.mark.parametrize("requested,expected", [
    ("2025-06-18", "2025-06-18"), ("2025-03-26", "2025-03-26"), ("2024-11-05", "2024-11-05"),
    ("2099-01-01", mcp_server.SUPPORTED_VERSIONS[0]), (None, mcp_server.SUPPORTED_VERSIONS[0]),
])
def test_initialize_negotiates_the_protocol_version(client, requested, expected):
    params = {"clientInfo": {"name": "x", "version": "1"}, "capabilities": {}}
    if requested:
        params["protocolVersion"] = requested
    result = rpc(client, "initialize", params).json()["result"]
    assert result["protocolVersion"] == expected
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "voice-vault"


def test_ping_returns_an_empty_result(client):
    assert rpc(client, "ping", id="abc").json() == {"jsonrpc": "2.0", "id": "abc", "result": {}}


@pytest.mark.parametrize("method", ["resources/list", "prompts/list", "tools/delete", "nope"])
def test_unknown_methods_are_32601(client, method):
    assert rpc(client, method).json()["error"]["code"] == -32601


def test_tools_list_names_two_read_only_tools(client):
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["list_recordings", "get_recording"]
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True and tool["annotations"]["destructiveHint"] is False
        assert tool["inputSchema"]["type"] == "object" and "untrusted" in tool["description"]


# --- list_recordings -------------------------------------------------------------
def test_req_003_list_recordings_lists_newest_first_with_excerpt(client, seed):
    for i in range(3):
        seed(index=i)
    text = text_of(call(client, "list_recordings"))
    lines = [l for l in text.splitlines() if l.startswith("id ")]
    assert len(lines) == 3 and "Title 2" in lines[0] and "Title 0" in lines[2]
    assert "Summary 2" in text and "verified" in lines[0]


def test_req_003_list_shows_a_plain_readable_excerpt(client, seed):
    seed(index=1, summary="> Datum: heute\n## Notizen\n- **Wichtig**: erster Punkt\n- [ ] zweiter Punkt")
    text = text_of(call(client, "list_recordings"))
    assert "Datum: heute \u00b7 Notizen \u00b7 Wichtig: erster Punkt \u00b7 zweiter Punkt" in text
    assert "##" not in text and "**" not in text and "\n>" not in text


def test_list_with_no_match_says_so(client):
    assert text_of(call(client, "list_recordings")) == "No recordings found."


def test_req_003_search_finds_transcript_words_case_insensitively(client, session_factory):
    with session_factory() as s:
        rec = add_recording(s, 1, segments=0)
        s.add(Segment(recording_id=rec.id, idx=0, speaker="A", start_ms=0, text="Die Kündigungsfrist beträgt drei Monate."))
        add_recording(s, 2)
        s.commit()
    text = text_of(call(client, "list_recordings", {"query": "kündigungsfrist"}))
    assert "Title 1" in text and "Title 2" not in text


def test_limit_and_cursor_page_through_all_recordings(client, seed):
    for i in range(5):
        seed(index=i, segments=0)
    first = text_of(call(client, "list_recordings", {"limit": 2}))
    cursor = first.split("next_cursor: ")[1].strip()
    second = text_of(call(client, "list_recordings", {"limit": 2, "cursor": cursor}))
    cursor2 = second.split("next_cursor: ")[1].strip()
    third = text_of(call(client, "list_recordings", {"limit": 2, "cursor": cursor2}))
    titles = [l.split(" | ")[-1] for t in (first, second, third) for l in t.splitlines() if l.startswith("id ")]
    assert titles == [f"Title {i}" for i in (4, 3, 2, 1, 0)] and "next_cursor" not in third


def test_since_and_until_filter_by_start_date(client, seed):
    for i, day in enumerate((1, 5, 10)):
        seed(index=i, started=dt.datetime(2026, 10, day, 12, 0, tzinfo=dt.timezone.utc), segments=0)
    both = text_of(call(client, "list_recordings", {"since": "2026-10-05", "until": "2026-10-05"}))
    assert "Title 1" in both and "Title 0" not in both and "Title 2" not in both  # a bare until-date includes that day
    only_after = text_of(call(client, "list_recordings", {"since": "2026-10-06"}))
    assert "Title 2" in only_after and "Title 1" not in only_after


@pytest.mark.parametrize("arguments", [
    {"limit": 0}, {"limit": 51}, {"limit": "5"}, {"limit": True}, {"limit": 1.5}, {"query": 5}, {"query": "x" * 201},
    {"since": "yesterday"}, {"until": "2026-13-45"}, {"cursor": "garbage"}, {"cursor": 5}, {"bogus": 1},
])
def test_req_003_invalid_list_arguments_are_32602(client, arguments):
    assert call(client, "list_recordings", arguments).json()["error"]["code"] == -32602


# --- get_recording ---------------------------------------------------------------
def test_req_003_get_recording_returns_summary_and_timestamped_transcript(client, seed):
    rec_id = seed(index=1, segments=3)
    text = text_of(call(client, "get_recording", {"id": rec_id}))
    assert "# Title 1" in text and "## Summary\nSummary 1" in text
    assert "[00:00] Speaker 0: text 1-0" in text and "[01:01] Speaker 1: text 1-1" in text
    assert "[02:02] Speaker 0: text 1-2" in text and "segments 1 to 3 of 3" in text
    assert "next_segment_offset" not in text


def test_long_transcripts_come_in_slices(client, seed):
    rec_id = seed(index=1, segments=10)
    first = text_of(call(client, "get_recording", {"id": rec_id, "segment_limit": 4}))
    assert "segments 1 to 4 of 10" in first and "next_segment_offset: 4" in first and "text 1-3" in first
    last = text_of(call(client, "get_recording", {"id": rec_id, "segment_offset": 8, "segment_limit": 4}))
    assert "segments 9 to 10 of 10" in last and "text 1-9" in last and "next_segment_offset" not in last


def test_offset_beyond_the_end_returns_an_empty_slice(client, seed):
    rec_id = seed(index=1, segments=2)
    assert "no segments at offset 5 of 2" in text_of(call(client, "get_recording", {"id": rec_id, "segment_offset": 5}))


def test_unknown_recording_is_a_tool_error_not_a_protocol_error(client):
    response = call(client, "get_recording", {"id": 999}).json()["result"]
    assert response["isError"] is True and "No recording with id 999" in response["content"][0]["text"]


def test_discarded_recording_is_not_readable(client, session_factory, seed):
    import recording_service as svc
    rec_id = seed(index=1)
    with session_factory() as s:
        svc.discard(s, rec_id, dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc))
    assert call(client, "get_recording", {"id": rec_id}).json()["result"]["isError"] is True
    assert text_of(call(client, "list_recordings")) == "No recordings found."


@pytest.mark.parametrize("arguments", [
    {}, {"id": "1"}, {"id": 0}, {"id": -1}, {"id": 1.5}, {"id": True}, {"id": 1, "segment_limit": 0},
    {"id": 1, "segment_limit": 2001}, {"id": 1, "segment_offset": -1}, {"id": 1, "extra": 1},
])
def test_req_003_invalid_get_arguments_are_32602(client, arguments):
    assert call(client, "get_recording", arguments).json()["error"]["code"] == -32602


@pytest.mark.parametrize("params", [{}, {"name": 5}, {"name": "delete_recording", "arguments": {}},
                                    {"name": "list_recordings", "arguments": "x"}, {"name": "list_recordings", "arguments": []}])
def test_invalid_tool_calls_are_32602(client, params):
    assert rpc(client, "tools/call", params).json()["error"]["code"] == -32602


def test_params_must_be_an_object(client):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]}, headers=AUTH)
    assert response.json()["error"]["code"] == -32602


# --- safety properties -------------------------------------------------------------
def test_req_003_tools_never_change_the_database(client, session_factory, seed):
    rec_id = seed(index=1, segments=3)
    seed(index=2)

    def snapshot():
        with session_factory() as s:
            recs = [(r.id, r.title, r.summary, r.discarded_at, r.trashed_at, r.deleted_at)
                    for r in s.scalars(select(Recording).order_by(Recording.id))]
            return recs, s.query(Segment).count()

    before = snapshot()
    call(client, "list_recordings", {"query": "Title"})
    call(client, "get_recording", {"id": rec_id})
    call(client, "get_recording", {"id": 999})
    assert snapshot() == before


def test_req_003_recording_text_never_reaches_the_log(client, seed, caplog):
    rec_id = seed(index=1, summary="TOPSECRET-SUMMARY", title="TOPSECRET-TITLE")
    with caplog.at_level(logging.DEBUG):
        call(client, "get_recording", {"id": rec_id})
        call(client, "list_recordings", {"query": "TOPSECRET-QUERY"})
    assert "TOPSECRET" not in caplog.text
    assert "tool=get_recording" in caplog.text and "result_chars=" in caplog.text


def test_internal_errors_do_not_leak_details(session_factory):
    def broken():
        raise RuntimeError("password=hunter2 at /srv/secret")
    client = TestClient(mcp_server.create_app(make_cfg(), broken), base_url="http://localhost")
    response = call(client, "list_recordings").json()
    assert response["error"]["code"] == -32603 and "hunter2" not in json.dumps(response)


# --- tools module ---------------------------------------------------------------------
@pytest.mark.parametrize("value,name,next_day,expected", [
    ("", "since", False, None),
    ("2026-10-05T12:00:00+00:00", "since", False, dt.datetime(2026, 10, 5, 12, tzinfo=dt.timezone.utc)),
    ("2026-10-05T12:00:00Z", "since", False, dt.datetime(2026, 10, 5, 12, tzinfo=dt.timezone.utc)),
])
def test_parse_when_with_explicit_zone(value, name, next_day, expected):
    assert mcp_tools.parse_when(value, name, next_day) == expected


def test_parse_when_date_is_local_midnight_and_until_date_is_the_next_midnight():
    since = mcp_tools.parse_when("2026-10-05", "since")
    until = mcp_tools.parse_when("2026-10-05", "until", date_means_next_day=True)
    assert until - since == dt.timedelta(days=1)
    assert since.astimezone(mcp_tools.LOCAL_TZ).hour == 0


def test_parse_when_with_time_and_no_zone_is_local_and_not_shifted_by_until():
    moment = mcp_tools.parse_when("2026-10-05T08:30", "until", date_means_next_day=True)
    assert moment.astimezone(mcp_tools.LOCAL_TZ).strftime("%Y-%m-%d %H:%M") == "2026-10-05 08:30"


@pytest.mark.parametrize("value", ["yesterday", "05.10.2026", "2026-13-01", "12:00"])
def test_parse_when_rejects_non_iso_values(value):
    with pytest.raises(mcp_tools.ToolArgumentError):
        mcp_tools.parse_when(value, "since")
