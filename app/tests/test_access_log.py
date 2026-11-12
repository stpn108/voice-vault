"""Access log middleware (D-070): one line per request, no query strings, no secrets."""
import asyncio
import logging
import re

import pytest

from access_log import AccessLogMiddleware

LINE = re.compile(
    r'^(?P<peer>\S+) - - \[(?P<ts>\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\] '
    r'"(?P<request>[^"]*)" (?P<status>\d{3}) (?P<bytes>\d+) "(?P<referer>[^"]*)" "(?P<agent>[^"]*)" '
    r'xff="(?P<xff>[^"]*)" rt=(?P<rt>\d+\.\d{3}) svc=(?P<svc>\S+)$'
)


def make_app(status=200, body=b"hello"):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": body[:2], "more_body": True})
        await send({"type": "http.response.body", "body": body[2:]})
    return app


def call(middleware, path="/", query=b"", headers=None, client=("203.0.113.7", 51000), method="GET"):
    scope = {"type": "http", "method": method, "path": path, "query_string": query, "http_version": "1.1",
             "headers": headers or [], "client": client}

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    asyncio.run(middleware(scope, receive, send))


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.INFO, logger="access")
    return lambda: [r.getMessage() for r in caplog.records if r.name == "access"]


def test_access_log_line_format(lines):
    headers = [(b"referer", b"https://vault.example/list"), (b"user-agent", b"Claude/1.0"),
               (b"x-forwarded-for", b"198.51.100.1, 10.0.0.1")]
    call(AccessLogMiddleware(make_app(), skip="", service="web"), "/recordings/5", headers=headers)
    [line] = lines()
    m = LINE.match(line)
    assert m, line
    assert m["peer"] == "203.0.113.7"
    assert m["request"] == "GET /recordings/5 HTTP/1.1"
    assert m["referer"] == "https://vault.example/list"
    assert m["agent"] == "Claude/1.0"
    assert m["xff"] == "198.51.100.1, 10.0.0.1"
    assert m["svc"] == "web"


def test_access_log_drops_query_string_from_path_and_referer(lines):
    headers = [(b"referer", b"https://vault.example/authorize?state=STATE123&code_challenge=CH")]
    call(AccessLogMiddleware(make_app(), skip=""), "/callback", query=b"code=SECRETCODE&state=STATE123",
         headers=headers)
    [line] = lines()
    assert '"GET /callback HTTP/1.1"' in line
    assert '"https://vault.example/authorize"' in line
    for secret in ("SECRETCODE", "STATE123", "code_challenge", "?"):
        assert secret not in line


def test_access_log_never_logs_authorization_or_cookies(lines):
    headers = [(b"authorization", b"Bearer TOPSECRETTOKEN"), (b"cookie", b"session=COOKIEVALUE")]
    call(AccessLogMiddleware(make_app(), skip=""), "/mcp", headers=headers, method="POST")
    [line] = lines()
    assert "TOPSECRETTOKEN" not in line and "COOKIEVALUE" not in line


def test_access_log_missing_headers_and_client_are_dashes(lines):
    call(AccessLogMiddleware(make_app(), skip="", service="mcp"), "/mcp", client=None)
    m = LINE.match(lines()[0])
    assert (m["peer"], m["referer"], m["agent"], m["xff"]) == ("-", "-", "-", "-")


@pytest.mark.parametrize("status,body", [(200, b"hello"), (404, b"not here!"), (204, b"")])
def test_access_log_status_and_byte_count(lines, status, body):
    call(AccessLogMiddleware(make_app(status, body), skip=""))
    m = LINE.match(lines()[0])
    assert (int(m["status"]), int(m["bytes"])) == (status, len(body))


def test_access_log_duration_present(lines):
    call(AccessLogMiddleware(make_app(), skip=""))
    assert float(LINE.match(lines()[0])["rt"]) >= 0


@pytest.mark.parametrize("path", ["/health", "/healthz"])
def test_access_log_skips_health_paths_by_default(lines, monkeypatch, path):
    monkeypatch.delenv("ACCESS_LOG_SKIP", raising=False)
    call(AccessLogMiddleware(make_app()), path)
    assert lines() == []


def test_access_log_skip_list_from_env(lines, monkeypatch):
    monkeypatch.setenv("ACCESS_LOG_SKIP", "/ping, /status")
    middleware = AccessLogMiddleware(make_app())
    for path in ("/ping", "/status", "/healthz"):
        call(middleware, path)
    assert len(lines()) == 1 and "/healthz" in lines()[0]


def test_access_log_service_name_defaults_to_env_then_hostname(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "mcp")
    assert AccessLogMiddleware(make_app()).service == "mcp"
    monkeypatch.delenv("SERVICE_NAME")
    assert AccessLogMiddleware(make_app()).service


def test_access_log_neutralises_control_characters(lines):
    # A forged newline in a header must not start a second, fake log line.
    call(AccessLogMiddleware(make_app(), skip=""), headers=[(b"user-agent", b"x\nfake 200 line")])
    [line] = lines()
    assert "\n" not in line


def test_access_log_written_when_app_raises(lines):
    async def broken(scope, receive, send):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        call(AccessLogMiddleware(broken, skip=""))
    assert LINE.match(lines()[0])["status"] == "500"


@pytest.mark.parametrize("module", ["webapp", "mcp_server"])
def test_access_log_installed_outermost(module, session_factory):
    if module == "webapp":
        import webapp
        app = webapp.app
    else:
        import mcp_server
        from tests.mcp_helpers import make_cfg
        app = mcp_server.create_app(make_cfg(), session_factory)
    assert app.user_middleware[0].cls is AccessLogMiddleware


def test_access_log_mcp_request_end_to_end(lines, session_factory):
    import mcp_server
    from fastapi.testclient import TestClient
    from tests.mcp_helpers import make_cfg
    client = TestClient(mcp_server.create_app(make_cfg(), session_factory), base_url="http://localhost")
    client.post("/mcp?token=LEAK", json={}, headers={"authorization": "Bearer WRONGTOKEN"})
    client.get("/healthz")
    [line] = lines()
    assert '"POST /mcp HTTP/1.1" 401 ' in line
    assert "LEAK" not in line and "WRONGTOKEN" not in line
