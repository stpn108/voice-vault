"""OAuth for the MCP server (REQ-005, D-012): discovery, login, tokens and their misuse."""
import base64
import dataclasses
import datetime as dt
import hashlib
import logging
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import mcp_oauth
import mcp_oauth_web
import mcp_server
from config import ConfigError, load_config
from database import OAuthCode, OAuthToken
from tests.helpers import add_recording
from utils import now_utc

PUBLIC = "https://mcp.example.org"
CLIENT_ID = "claude-client-001"
PASSWORD = "correct horse battery staple 99"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
STATIC = "s" * 40
NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone.utc)


def make_cfg(**overrides):
    base = dict(
        mcp_tokens=(STATIC,), mcp_public_url=PUBLIC, mcp_oauth_client_id=CLIENT_ID,
        mcp_oauth_client_secret="", mcp_oauth_password=PASSWORD,
        mcp_oauth_redirect_uris=(CALLBACK, "http://localhost/callback"),
        mcp_allowed_hosts=(), mcp_allowed_origins=(),
    )
    base.update(overrides)
    return dataclasses.replace(load_config(), **base)


class Sleeper:
    def __init__(self):
        self.delays = []

    async def __call__(self, seconds):
        self.delays.append(seconds)


def make_client(session_factory, sleeper=None, **cfg_overrides):
    app = mcp_server.create_app(make_cfg(**cfg_overrides), session_factory, sleeper or Sleeper())
    return TestClient(app, base_url=PUBLIC, follow_redirects=False)


@pytest.fixture
def sleeper():
    return Sleeper()


@pytest.fixture
def client(session_factory, sleeper):
    return make_client(session_factory, sleeper)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_params(challenge, **overrides):
    params = dict(response_type="code", client_id=CLIENT_ID, redirect_uri=CALLBACK, code_challenge=challenge,
                  code_challenge_method="S256", state="state-123")
    params.update(overrides)
    return {k: v for k, v in params.items() if v is not None}


def login(client, challenge, password=PASSWORD, action="allow", **overrides):
    page = client.get("/authorize", params=authorize_params(challenge, **overrides))
    assert page.status_code == 200, page.text
    req = re.search(r'name="req" value="([^"]+)"', page.text).group(1)
    sig = re.search(r'name="sig" value="([^"]+)"', page.text).group(1)
    return client.post("/authorize", data={"req": req, "sig": sig, "password": password, "action": action})


def code_from(response):
    assert response.status_code == 302, response.text
    return parse_qs(urlparse(response.headers["location"]).query)["code"][0]


def token_request(client, **fields):
    return client.post("/token", data=fields)


def get_tokens(client, **overrides):
    verifier, challenge = pkce()
    code = code_from(login(client, challenge))
    fields = dict(grant_type="authorization_code", code=code, redirect_uri=CALLBACK, code_verifier=verifier,
                  client_id=CLIENT_ID)
    fields.update(overrides)
    return token_request(client, **fields)


def mcp_ping(client, token):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"Authorization": f"Bearer {token}"})


# --- configuration -------------------------------------------------------------------------
@pytest.mark.parametrize("overrides", [
    dict(mcp_oauth_password="short"), dict(mcp_oauth_password=""), dict(mcp_oauth_client_id="short"), dict(mcp_oauth_client_id=""),
    dict(mcp_public_url=""), dict(mcp_public_url="http://mcp.example.org"), dict(mcp_public_url="https://mcp.example.org/mcp"),
    dict(mcp_public_url="https://mcp.example.org?x=1"), dict(mcp_oauth_redirect_uris=()),
    dict(mcp_oauth_redirect_uris=("http://evil.example/callback",)),
    dict(mcp_oauth_redirect_uris=("https://claude.ai/cb#frag",)), dict(mcp_oauth_redirect_uris=("ftp://x.example/cb",)),
    dict(mcp_oauth_redirect_uris=("https://user:pw@claude.ai/cb",)),
])
def test_req_005_unsafe_oauth_configuration_is_refused(session_factory, overrides):
    with pytest.raises(ConfigError):
        mcp_server.create_app(make_cfg(**overrides), session_factory)


def test_oauth_alone_is_enough_to_start(session_factory):
    mcp_server.create_app(make_cfg(mcp_tokens=()), session_factory)


def test_loopback_http_public_url_is_allowed_for_local_testing(session_factory):
    mcp_server.create_app(make_cfg(mcp_public_url="http://localhost:8000"), session_factory)


@pytest.mark.parametrize("value,expected", [
    ("mcp.example.org", "mcp.example.org"), ("https://mcp.example.org", "mcp.example.org"),
    ("https://MCP.example.org/mcp", "mcp.example.org"), ("mcp.example.org:8443", "mcp.example.org"),
    ("  mcp.example.org/  ", "mcp.example.org"), ("[::1]", "[::1]"), ("localhost", "localhost"),
])
def test_host_names_may_be_pasted_as_urls(value, expected):
    from config import host_name
    assert host_name(value) == expected


def test_req_005_pasted_url_in_allowed_hosts_still_works(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "https://mcp.example.org/mcp, other.example.org:8443")
    assert load_config().mcp_allowed_hosts == ("mcp.example.org", "other.example.org")


# --- redirect URIs -------------------------------------------------------------------------
@pytest.mark.parametrize("uri,allowed", [
    (CALLBACK, True), (CALLBACK + "?x=1", False), (CALLBACK + "/", False), ("https://claude.ai/api/mcp/auth_callbackx", False),
    ("https://claude.ai.evil.com/api/mcp/auth_callback", False), ("http://claude.ai/api/mcp/auth_callback", False),
    ("http://localhost/callback", True), ("http://localhost:3118/callback", True), ("http://localhost:3118/other", False),
    ("http://127.0.0.1:3118/callback", False), ("https://localhost:3118/callback", False),
    ("http://localhost.evil.com/callback", False), ("", False), ("not a uri", False),
    ("https://user@claude.ai/api/mcp/auth_callback", False), (CALLBACK + "#frag", False),
])
def test_redirect_allowed(uri, allowed):
    assert mcp_oauth_web.redirect_allowed(uri, (CALLBACK, "http://localhost/callback")) is allowed


# --- discovery -----------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"])
def test_req_005_protected_resource_metadata(client, path):
    doc = client.get(path).json()
    assert doc["resource"] == f"{PUBLIC}/mcp" and doc["authorization_servers"] == [PUBLIC]
    assert doc["bearer_methods_supported"] == ["header"]


def test_req_005_authorization_server_metadata(client):
    doc = client.get("/.well-known/oauth-authorization-server").json()
    assert doc["issuer"] == PUBLIC
    assert doc["authorization_endpoint"] == f"{PUBLIC}/authorize" and doc["token_endpoint"] == f"{PUBLIC}/token"
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["response_types_supported"] == ["code"] and "offline_access" in doc["scopes_supported"]
    assert set(doc["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert doc["token_endpoint_auth_methods_supported"] == ["none"]
    assert "registration_endpoint" not in doc  # no open client registration


def test_metadata_lists_secret_methods_when_a_client_secret_is_set(session_factory):
    doc = make_client(session_factory, mcp_oauth_client_secret="x" * 30).get("/.well-known/oauth-authorization-server").json()
    assert doc["token_endpoint_auth_methods_supported"] == ["client_secret_post", "client_secret_basic"]


def test_req_005_unauthenticated_mcp_call_points_to_the_metadata(client):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401
    assert f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource"' in response.headers["www-authenticate"]


@pytest.mark.parametrize("setting", [
    dict(mcp_oauth_client_secret="x" * 30), dict(mcp_oauth_client_id=CLIENT_ID), dict(mcp_public_url=PUBLIC),
])
def test_any_single_oauth_setting_without_a_password_is_refused(session_factory, setting):
    cleared = dict(mcp_oauth_password="", mcp_oauth_client_id="", mcp_oauth_client_secret="", mcp_public_url="")
    with pytest.raises(ConfigError):
        mcp_server.create_app(make_cfg(**{**cleared, **setting}), session_factory)


def test_without_oauth_there_are_no_oauth_endpoints_and_no_pointer(session_factory):
    cleared = dict(mcp_oauth_password="", mcp_oauth_client_id="", mcp_oauth_client_secret="", mcp_public_url="")
    client = TestClient(mcp_server.create_app(make_cfg(**cleared), session_factory), base_url="http://localhost")
    for path in ("/authorize", "/token", "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server"):
        assert client.get(path).status_code in (404, 405)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401 and "resource_metadata" not in response.headers["www-authenticate"]


# --- authorization request ---------------------------------------------------------------
def test_req_005_login_page_names_the_redirect_host_and_is_not_cacheable(client):
    _, challenge = pkce()
    page = client.get("/authorize", params=authorize_params(challenge))
    assert page.status_code == 200 and "claude.ai" in page.text and 'type="password"' in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["x-frame-options"] == "DENY" and "default-src 'none'" in page.headers["content-security-policy"]


@pytest.mark.parametrize("overrides", [
    dict(client_id="someone-else"), dict(client_id=None), dict(redirect_uri="https://evil.example/cb"),
    dict(redirect_uri=None), dict(redirect_uri=CALLBACK + "x"), dict(state="x" * 513),
])
def test_req_005_unknown_client_or_redirect_never_redirects(client, overrides):
    _, challenge = pkce()
    response = client.get("/authorize", params=authorize_params(challenge, **overrides))
    assert response.status_code == 400 and "location" not in response.headers


@pytest.mark.parametrize("overrides,error", [
    (dict(response_type="token"), "unsupported_response_type"), (dict(response_type=None), "unsupported_response_type"),
    (dict(code_challenge_method="plain"), "invalid_request"), (dict(code_challenge_method=None), "invalid_request"),
    (dict(code_challenge=None), "invalid_request"), (dict(code_challenge="short"), "invalid_request"),
    (dict(resource="https://other.example/mcp"), "invalid_target"),
])
def test_req_005_bad_requests_redirect_with_an_error(client, overrides, error):
    _, challenge = pkce()
    response = client.get("/authorize", params=authorize_params(challenge, **overrides))
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["error"] == [error] and query["state"] == ["state-123"] and query["iss"] == [PUBLIC]


def test_matching_resource_is_accepted(client):
    _, challenge = pkce()
    assert client.get("/authorize", params=authorize_params(challenge, resource=f"{PUBLIC}/mcp")).status_code == 200


def test_loopback_redirect_with_any_port_is_accepted(client):
    _, challenge = pkce()
    uri = "http://localhost:3118/callback"
    assert client.get("/authorize", params=authorize_params(challenge, redirect_uri=uri)).status_code == 200


# --- login ---------------------------------------------------------------------------------
def test_req_005_correct_password_redirects_with_code_state_and_issuer(client, session_factory):
    _, challenge = pkce()
    response = login(client, challenge)
    assert response.status_code == 302 and response.headers["location"].startswith(CALLBACK + "?")
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["state"] == ["state-123"] and query["iss"] == [PUBLIC] and len(query["code"][0]) >= 43
    with session_factory() as s:
        stored = s.scalars(select(OAuthCode)).all()
        assert len(stored) == 1 and query["code"][0] not in (stored[0].code_hash, stored[0].redirect_uri)
        assert stored[0].code_hash == hashlib.sha256(query["code"][0].encode()).hexdigest()


def test_req_005_wrong_password_gives_no_code_and_gets_slower(client, session_factory, sleeper):
    _, challenge = pkce()
    for _ in range(3):
        response = login(client, challenge, password="wrong password " * 3)
        assert response.status_code == 401 and "location" not in response.headers
        assert "Das Passwort stimmt nicht." in response.text
    assert sleeper.delays == [1, 2, 4]
    with session_factory() as s:
        assert s.scalars(select(OAuthCode)).all() == []


def test_delay_is_capped_and_resets_after_a_successful_login(client, sleeper):
    _, challenge = pkce()
    for _ in range(8):
        login(client, challenge, password="nope" * 8)
    assert max(sleeper.delays) == mcp_oauth_web.MAX_DELAY_SECONDS
    assert login(client, challenge).status_code == 302
    login(client, challenge, password="nope" * 8)
    assert sleeper.delays[-1] == 1


def test_password_is_compared_exactly(client):
    _, challenge = pkce()
    for wrong in (PASSWORD + " ", PASSWORD.upper(), PASSWORD[:-1], ""):
        assert login(client, challenge, password=wrong).status_code == 401


def test_req_005_deny_redirects_with_access_denied_and_creates_nothing(client, session_factory):
    _, challenge = pkce()
    response = login(client, challenge, action="deny")
    assert parse_qs(urlparse(response.headers["location"]).query)["error"] == ["access_denied"]
    with session_factory() as s:
        assert s.scalars(select(OAuthCode)).all() == []


def test_req_005_tampered_form_is_rejected(client):
    _, challenge = pkce()
    page = client.get("/authorize", params=authorize_params(challenge))
    req = re.search(r'name="req" value="([^"]+)"', page.text).group(1)
    sig = re.search(r'name="sig" value="([^"]+)"', page.text).group(1)
    forged = base64.urlsafe_b64encode(base64.urlsafe_b64decode(req).replace(CALLBACK.encode(), b"http://localhost/callback")).decode()
    for data in ({"req": forged, "sig": sig}, {"req": req, "sig": "0" * 64}, {"req": req, "sig": ""}, {"req": "x", "sig": sig}, {}):
        response = client.post("/authorize", data={**data, "password": PASSWORD, "action": "allow"})
        assert response.status_code == 400 and "location" not in response.headers


def test_a_form_from_another_server_instance_is_rejected(session_factory, client):
    _, challenge = pkce()
    page = client.get("/authorize", params=authorize_params(challenge))
    req = re.search(r'name="req" value="([^"]+)"', page.text).group(1)
    sig = re.search(r'name="sig" value="([^"]+)"', page.text).group(1)
    other = make_client(session_factory)  # a restart: new signing key
    assert other.post("/authorize", data={"req": req, "sig": sig, "password": PASSWORD, "action": "allow"}).status_code == 400


def test_req_005_expired_form_is_rejected(client, monkeypatch):
    _, challenge = pkce()
    page = client.get("/authorize", params=authorize_params(challenge))
    req = re.search(r'name="req" value="([^"]+)"', page.text).group(1)
    sig = re.search(r'name="sig" value="([^"]+)"', page.text).group(1)
    real = mcp_oauth_web.time.time()
    monkeypatch.setattr(mcp_oauth_web.time, "time", lambda: real + mcp_oauth_web.FORM_TTL_SECONDS + 5)
    response = client.post("/authorize", data={"req": req, "sig": sig, "password": PASSWORD, "action": "allow"})
    assert response.status_code == 400 and "abgelaufen" in response.text


def test_req_005_password_never_reaches_the_log(client, caplog):
    _, challenge = pkce()
    with caplog.at_level(logging.DEBUG):
        login(client, challenge, password="my-wrong-password-123456")
        login(client, challenge)
    assert "my-wrong-password-123456" not in caplog.text and PASSWORD not in caplog.text
    assert "OAuth login failed" in caplog.text


# --- token endpoint ------------------------------------------------------------------------
def test_req_005_code_exchange_returns_a_token_pair_and_stores_only_hashes(client, session_factory):
    response = get_tokens(client)
    body = response.json()
    assert response.status_code == 200 and body["token_type"] == "Bearer" and body["expires_in"] == 3600
    assert response.headers["cache-control"] == "no-store" and response.headers["pragma"] == "no-cache"
    assert "offline_access" in body["scope"] and body["access_token"] != body["refresh_token"]
    with session_factory() as s:
        rows = s.scalars(select(OAuthToken)).all()
        assert {r.kind for r in rows} == {"access", "refresh"}
        assert body["access_token"] not in {r.token_hash for r in rows} and body["refresh_token"] not in {r.token_hash for r in rows}
        assert hashlib.sha256(body["access_token"].encode()).hexdigest() in {r.token_hash for r in rows}


def test_req_005_the_access_token_opens_the_mcp_endpoint(client):
    token = get_tokens(client).json()["access_token"]
    assert mcp_ping(client, token).status_code == 200
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                        headers={"Authorization": f"Bearer {token}"}).json()
    assert [t["name"] for t in tools["result"]["tools"]][:2] == ["list_recordings", "get_recording"]


def test_the_refresh_token_does_not_open_the_mcp_endpoint(client):
    assert mcp_ping(client, get_tokens(client).json()["refresh_token"]).status_code == 401


def test_static_token_still_works_with_oauth_enabled(client):
    assert mcp_ping(client, STATIC).status_code == 200


def test_unknown_token_is_rejected(client):
    assert mcp_ping(client, secrets.token_urlsafe(32)).status_code == 401


@pytest.mark.parametrize("override", [
    dict(code_verifier="x" * 43), dict(code_verifier="short"), dict(redirect_uri="http://localhost/callback"),
    dict(code="unknown-code-" + "x" * 30),
])
def test_req_005_bad_code_exchange_is_invalid_grant(client, override):
    response = get_tokens(client, **override)
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


def test_req_005_a_replayed_code_fails_and_revokes_the_tokens_it_produced(client):
    verifier, challenge = pkce()
    code = code_from(login(client, challenge))
    fields = dict(grant_type="authorization_code", code=code, redirect_uri=CALLBACK, code_verifier=verifier, client_id=CLIENT_ID)
    first = token_request(client, **fields)
    assert first.status_code == 200 and mcp_ping(client, first.json()["access_token"]).status_code == 200
    replay = token_request(client, **fields)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert mcp_ping(client, first.json()["access_token"]).status_code == 401
    refresh = token_request(client, grant_type="refresh_token", refresh_token=first.json()["refresh_token"], client_id=CLIENT_ID)
    assert refresh.status_code == 400


def test_an_expired_code_is_refused(session_factory, client):
    verifier, challenge = pkce()
    with session_factory() as s:
        code = mcp_oauth.issue_code(s, CALLBACK, challenge, now_utc() - dt.timedelta(minutes=5))
    response = token_request(client, grant_type="authorization_code", code=code, redirect_uri=CALLBACK,
                             code_verifier=verifier, client_id=CLIENT_ID)
    assert response.json()["error"] == "invalid_grant" and "expired" in response.json()["error_description"]


@pytest.mark.parametrize("fields,error", [
    ({}, "unsupported_grant_type"), ({"grant_type": "password"}, "unsupported_grant_type"),
    ({"grant_type": "client_credentials"}, "unsupported_grant_type"),
    ({"grant_type": "authorization_code"}, "invalid_request"),
    ({"grant_type": "authorization_code", "code": "c"}, "invalid_request"),
    ({"grant_type": "refresh_token"}, "invalid_request"),
])
def test_req_005_incomplete_token_requests(client, fields, error):
    response = token_request(client, client_id=CLIENT_ID, **fields)
    assert response.status_code == 400 and response.json()["error"] == error


def test_json_bodies_are_not_a_token_request(client):
    response = client.post("/token", json={"grant_type": "refresh_token", "refresh_token": "x", "client_id": CLIENT_ID})
    assert response.status_code == 401 and response.json()["error"] == "invalid_client"


@pytest.mark.parametrize("fields", [{}, {"client_id": "someone-else"}, {"client_id": CLIENT_ID.upper()}])
def test_req_005_unknown_client_is_invalid_client(client, fields):
    response = token_request(client, grant_type="refresh_token", refresh_token="x", **fields)
    assert response.status_code == 401 and response.json()["error"] == "invalid_client"
    assert response.headers["www-authenticate"] == "Basic"


def test_resource_must_match(client):
    response = get_tokens(client, resource="https://other.example/mcp")
    assert response.json()["error"] == "invalid_target"


def test_client_secret_is_required_when_configured(session_factory):
    secret = "client-secret-" + "z" * 20
    client = make_client(session_factory, mcp_oauth_client_secret=secret)
    verifier, challenge = pkce()

    def exchange(**extra):
        code = code_from(login(client, challenge))
        fields = dict(grant_type="authorization_code", code=code, redirect_uri=CALLBACK, code_verifier=verifier)
        return client.post("/token", data={**fields, **extra.pop("data", {})}, **extra)

    assert exchange(data={"client_id": CLIENT_ID}).status_code == 401
    assert exchange(data={"client_id": CLIENT_ID, "client_secret": "wrong"}).status_code == 401
    assert exchange(data={"client_id": CLIENT_ID, "client_secret": secret}).status_code == 200
    basic = base64.b64encode(f"{CLIENT_ID}:{secret}".encode()).decode()
    assert exchange(headers={"Authorization": f"Basic {basic}"}).status_code == 200
    bad_basic = base64.b64encode(f"{CLIENT_ID}:nope".encode()).decode()
    assert exchange(headers={"Authorization": f"Basic {bad_basic}"}).status_code == 401
    assert exchange(headers={"Authorization": "Basic !!!not-base64"}).status_code == 401


# --- refresh tokens ------------------------------------------------------------------------
def test_req_005_refresh_rotates_the_refresh_token(client):
    first = get_tokens(client).json()
    second = token_request(client, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=CLIENT_ID)
    body = second.json()
    assert second.status_code == 200 and body["refresh_token"] != first["refresh_token"]
    assert mcp_ping(client, body["access_token"]).status_code == 200


def test_req_005_reusing_a_rotated_refresh_token_revokes_the_whole_family(client):
    first = get_tokens(client).json()
    second = token_request(client, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=CLIENT_ID).json()
    reuse = token_request(client, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=CLIENT_ID)
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
    assert mcp_ping(client, second["access_token"]).status_code == 401
    again = token_request(client, grant_type="refresh_token", refresh_token=second["refresh_token"], client_id=CLIENT_ID)
    assert again.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("token", ["", "unknown-" + "x" * 40])
def test_unknown_refresh_token_is_invalid_grant(client, token):
    response = token_request(client, grant_type="refresh_token", refresh_token=token or "x", client_id=CLIENT_ID)
    assert response.json()["error"] == "invalid_grant"


def test_an_access_token_cannot_be_used_to_refresh(client):
    access = get_tokens(client).json()["access_token"]
    response = token_request(client, grant_type="refresh_token", refresh_token=access, client_id=CLIENT_ID)
    assert response.json()["error"] == "invalid_grant"


def test_req_005_expired_access_token_is_rejected(client, session_factory):
    body = get_tokens(client).json()
    with session_factory() as s:
        s.execute(update(OAuthToken).where(OAuthToken.kind == "access").values(expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)))
        s.commit()
    assert mcp_ping(client, body["access_token"]).status_code == 401
    refreshed = token_request(client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=CLIENT_ID)
    assert refreshed.status_code == 200 and mcp_ping(client, refreshed.json()["access_token"]).status_code == 200


def test_expired_refresh_token_is_invalid_grant(client, session_factory):
    body = get_tokens(client).json()
    with session_factory() as s:
        s.execute(update(OAuthToken).where(OAuthToken.kind == "refresh").values(expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)))
        s.commit()
    response = token_request(client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=CLIENT_ID)
    assert response.json()["error"] == "invalid_grant"


def test_req_005_revoke_all_logs_everything_out(client, session_factory):
    body = get_tokens(client).json()
    with session_factory() as s:
        assert mcp_oauth.revoke_all(s, NOW) == 2
    assert mcp_ping(client, body["access_token"]).status_code == 401
    assert token_request(client, grant_type="refresh_token", refresh_token=body["refresh_token"],
                         client_id=CLIENT_ID).json()["error"] == "invalid_grant"


def test_token_values_never_reach_the_log(client, caplog):
    with caplog.at_level(logging.DEBUG):
        body = get_tokens(client).json()
        mcp_ping(client, body["access_token"])
        token_request(client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=CLIENT_ID)
    assert body["access_token"] not in caplog.text and body["refresh_token"] not in caplog.text


# --- the complete flow, then a real tool call -------------------------------------------------
def test_req_005_full_flow_from_login_to_reading_a_recording(client, session_factory):
    with session_factory() as s:
        rec_id = add_recording(s, 1, segments=2).id
    tokens = get_tokens(client).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    result = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "get_recording", "arguments": {"id": rec_id}}}).json()
    assert "Title 1" in result["result"]["content"][0]["text"]
    refreshed = token_request(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=CLIENT_ID).json()
    again = client.post("/mcp", headers={"Authorization": f"Bearer {refreshed['access_token']}"},
                        json={"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert again.status_code == 200


# --- service level -------------------------------------------------------------------------
def test_pkce_matches_the_rfc_7636_test_vector():
    verifier, challenge = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert mcp_oauth.pkce_matches(verifier, challenge) is True
    assert mcp_oauth.pkce_matches(verifier, challenge[:-1] + "A") is False


@pytest.mark.parametrize("verifier", ["", "short", "x" * 42, "x" * 129, "a" * 43 + " ", "ä" * 50])
def test_pkce_rejects_malformed_verifiers(verifier):
    assert mcp_oauth.pkce_matches(verifier, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM") is False


@pytest.mark.parametrize("challenge,valid", [
    ("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", True), ("", False), ("short", False),
    ("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM=", False), ("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw+cM", False),
])
def test_is_valid_challenge(challenge, valid):
    assert mcp_oauth.is_valid_challenge(challenge) is valid


def test_purge_removes_rows_that_expired_long_ago_only(session_factory):
    with session_factory() as s:
        s.add(OAuthToken(token_hash="old", kind="access", family_id="f", expires_at=NOW - dt.timedelta(days=3)))
        s.add(OAuthToken(token_hash="recent", kind="access", family_id="f", expires_at=NOW - dt.timedelta(hours=1)))
        s.add(OAuthCode(code_hash="oldcode", redirect_uri="x", code_challenge="y", expires_at=NOW - dt.timedelta(days=3)))
        s.commit()
        mcp_oauth.purge_expired(s, NOW)
        s.commit()
        assert [t.token_hash for t in s.scalars(select(OAuthToken))] == ["recent"]
        assert s.scalars(select(OAuthCode)).all() == []
