"""Findings of the security review for the MCP endpoint (D-020)."""
import json

import pytest
from fastapi.testclient import TestClient

import mcp_server
from tests.mcp_helpers import AUTH, make_cfg, rpc


@pytest.fixture
def client(session_factory):
    return TestClient(mcp_server.create_app(make_cfg(), session_factory), base_url="http://localhost")


def raw(client, body):
    return client.post("/mcp", content=body, headers={**AUTH, "content-type": "application/json"})


def test_review_deeply_nested_json_is_a_parse_error_not_a_crash(client):
    response = raw(client, "[" * 30000)
    assert response.status_code == 400 and response.json()["error"]["code"] == -32700


@pytest.mark.parametrize("body", [
    '{"jsonrpc":"2.0","id":NaN,"method":"ping"}', '{"jsonrpc":"2.0","id":Infinity,"method":"ping"}',
    '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":-Infinity}}',
])
def test_review_nan_and_infinity_are_rejected_as_invalid_json(client, body):
    assert raw(client, body).status_code == 400


@pytest.mark.parametrize("request_id", [1.5, True, [1], {"a": 1}])
def test_review_ids_must_be_strings_or_integers(client, request_id):
    response = raw(client, json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "ping"}))
    assert response.json()["error"]["code"] == -32600


@pytest.mark.parametrize("request_id", [1, "abc", None])
def test_review_integer_string_and_null_ids_still_work(client, request_id):
    response = raw(client, json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "ping"}))
    assert response.json() == {"jsonrpc": "2.0", "id": request_id, "result": {}}


def test_review_a_batch_is_limited_in_length(client):
    pings = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(mcp_server.MAX_BATCH)]
    assert len(raw(client, json.dumps(pings)).json()) == mcp_server.MAX_BATCH
    too_many = pings + [{"jsonrpc": "2.0", "id": 99, "method": "ping"}]
    assert raw(client, json.dumps(too_many)).status_code == 400


def test_review_log_text_cannot_forge_lines():
    assert mcp_server.printable("a\r\nFORGED line\x1b[31m") == "a??FORGED line?[31m"
    assert len(mcp_server.printable("x" * 500)) == 80


def test_review_log_lines_for_odd_tool_names_stay_on_one_line(client, caplog):
    with caplog.at_level("INFO"):
        rpc(client, "tools/call", {"name": "bad\nname\r\nMCP tool call tool=forged", "arguments": {}})
    assert not [line for line in caplog.text.splitlines() if line.startswith("MCP tool call tool=forged")]


def test_review_the_instructions_say_what_the_server_can_write():
    assert "Read-only" not in mcp_server.INSTRUCTIONS and "nothing can be deleted" in mcp_server.INSTRUCTIONS
