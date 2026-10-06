"""Token storage and automatic renewal (REQ-004, D-005)."""
import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from database import PlaudSession
from plaud_auth import (
    DbTokenStore, PlaudAuth, PlaudAuthError, PlaudError, StoredTokens, redirect_host,
)
from tests.helpers import MemoryStore, make_jwt, refresh_response

BASE = "https://api-euc1.plaud.ai"
NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
NOW_EPOCH = NOW.timestamp()


def make_auth(handler, store=None, access="", refresh=""):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    store = store if store is not None else MemoryStore()
    return PlaudAuth(store, http, access, refresh, now_fn=lambda: NOW), store


def never(request):
    raise AssertionError("no request expected")


def test_first_load_seeds_store_from_environment():
    auth, store = make_auth(never, access="A1", refresh="R1")
    assert auth.access_token() == "A1" and auth.can_refresh()
    assert store.tokens.access_token == "A1" and store.tokens.refresh_token == "R1"


def test_access_token_only_seed_cannot_refresh():
    auth, _ = make_auth(never, access="A1")
    assert auth.access_token() == "A1" and not auth.can_refresh()


def test_stored_pair_wins_when_seed_is_unchanged():
    # After a rotation the database holds newer tokens than .env; a restart must keep them.
    first, store = make_auth(never, access="A1", refresh="R1")
    first.access_token()
    store.tokens = StoredTokens("A2", "R2", store.tokens.seed_fingerprint)
    restarted, _ = make_auth(never, store=store, access="A1", refresh="R1")
    assert restarted.access_token() == "A2" and restarted.tokens.refresh_token == "R2"


def test_changed_seed_replaces_stored_pair():
    # The owner pasted new tokens into .env: they must beat the old stored pair.
    first, store = make_auth(never, access="A1", refresh="R1")
    first.access_token()
    second, _ = make_auth(never, store=store, access="A9", refresh="R9")
    assert second.access_token() == "A9" and store.tokens.refresh_token == "R9"


def test_no_seed_and_empty_store_means_no_token():
    auth, _ = make_auth(never)
    assert auth.access_token() == "" and not auth.can_refresh()


def test_refresh_sends_refresh_cookie_and_stores_rotated_pair():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers["cookie"]
        seen["origin"] = request.headers["origin"]
        return refresh_response("A2", "R2")

    auth, store = make_auth(handler, access="A1", refresh="R1")
    assert auth.refresh(BASE) == BASE
    assert seen == {"url": f"{BASE}/auth/refresh-user-token", "cookie": "pld_urt=R1",
                    "origin": "https://web.plaud.ai"}
    assert (auth.access_token(), store.tokens.refresh_token) == ("A2", "R2")


def test_refresh_without_rotation_keeps_refresh_token():
    auth, store = make_auth(lambda r: refresh_response("A2"), access="A1", refresh="R1")
    auth.refresh(BASE)
    assert store.tokens.access_token == "A2" and store.tokens.refresh_token == "R1"


def test_refresh_ignores_clearing_cookie_even_when_it_comes_last():
    def handler(request):
        return httpx.Response(200, headers=[
            ("set-cookie", "pld_ut=A2; Path=/"),
            ("set-cookie", 'pld_ut=""; Max-Age=0; Path=/'),
        ], json={"status": 0})

    auth, _ = make_auth(handler, access="A1", refresh="R1")
    auth.refresh(BASE)
    assert auth.access_token() == "A2"


@pytest.mark.parametrize("code", [401, 403])
def test_refresh_rejected_raises_auth_error(code):
    auth, _ = make_auth(lambda r: httpx.Response(code), access="A1", refresh="R1")
    with pytest.raises(PlaudAuthError, match="rejected"):
        auth.refresh(BASE)


def test_refresh_without_cookie_raises_auth_error_with_plaud_message():
    auth, store = make_auth(
        lambda r: httpx.Response(200, json={"status": -2, "msg": "token invalid"}),
        access="A1", refresh="R1")
    with pytest.raises(PlaudAuthError, match="token invalid"):
        auth.refresh(BASE)
    assert store.tokens.access_token == "A1"  # nothing overwritten on failure


def test_refresh_without_refresh_token_raises():
    auth, _ = make_auth(never, access="A1")
    with pytest.raises(PlaudAuthError, match="no refresh token"):
        auth.refresh(BASE)


def test_refresh_network_error_is_plaud_error_not_auth_error():
    def handler(request):
        raise httpx.ConnectError("down")

    auth, _ = make_auth(handler, access="A1", refresh="R1")
    with pytest.raises(PlaudError) as exc:
        auth.refresh(BASE)
    assert not isinstance(exc.value, PlaudAuthError)


def test_refresh_5xx_is_plaud_error_not_auth_error():
    auth, _ = make_auth(lambda r: httpx.Response(503), access="A1", refresh="R1")
    with pytest.raises(PlaudError) as exc:
        auth.refresh(BASE)
    assert not isinstance(exc.value, PlaudAuthError)


def test_refresh_follows_one_region_redirect_and_returns_new_base():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "api-euc1.plaud.ai":
            return httpx.Response(200, json={"status": -302, "data": {"domains": {"api": "https://api-apse1.plaud.ai"}}})
        return refresh_response("A2", "R2")

    auth, _ = make_auth(handler, access="A1", refresh="R1")
    assert auth.refresh(BASE) == "https://api-apse1.plaud.ai"
    assert hosts == ["api-euc1.plaud.ai", "api-apse1.plaud.ai"]


@pytest.mark.parametrize("host", ["evil.example.com", "plaud.ai.evil.com", "http://x.attacker.net"])
def test_refresh_redirect_to_foreign_host_is_refused_and_cookie_never_sent_there(host):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(200, json={"status": -302, "data": {"domains": {"api": host}}})

    auth, _ = make_auth(handler, access="A1", refresh="R1")
    with pytest.raises(PlaudError):
        auth.refresh(BASE)
    assert hosts == ["api-euc1.plaud.ai"]


def test_refresh_redirect_loop_raises():
    auth, _ = make_auth(
        lambda r: httpx.Response(200, json={"status": -302, "data": {"domains": {"api": "api-apse1.plaud.ai"}}}),
        access="A1", refresh="R1")
    with pytest.raises(PlaudAuthError, match="loop"):
        auth.refresh(BASE)


def test_refresh_uses_refresh_token_rotated_by_another_process():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers["cookie"]
        return refresh_response("A3")

    auth, store = make_auth(handler, access="A1", refresh="R1")
    auth.access_token()
    store.tokens = StoredTokens("A2", "R2", store.tokens.seed_fingerprint)
    auth.refresh(BASE)
    assert seen["cookie"] == "pld_urt=R2"


@pytest.mark.parametrize("exp_in,iat_ago,expected", [
    (30 * 3600, 0, False),            # 24h token that was just issued... (30h left)
    (5 * 3600, 19 * 3600, True),      # 24h token, 5h left < 25% (6h)
    (7 * 3600, 17 * 3600, False),     # 24h token, 7h left
    (600, 86400 - 600, True),         # last 10 minutes
    (20 * 86400, 10 * 86400, False),  # 30d token with 20 days left
    (6 * 86400, 24 * 86400, True),    # 30d token, 6 days left < 7.5
])
def test_needs_refresh_by_remaining_lifetime(exp_in, iat_ago, expected):
    token = make_jwt(int(NOW_EPOCH + exp_in), iat=int(NOW_EPOCH - iat_ago))
    auth, _ = make_auth(never, access=token, refresh="R1")
    assert auth.needs_refresh(NOW_EPOCH) is expected


def test_needs_refresh_when_access_token_missing():
    auth, _ = make_auth(never, refresh="R1")
    assert auth.needs_refresh(NOW_EPOCH) is True


def test_unreadable_expiry_waits_for_a_401():
    auth, _ = make_auth(never, access="opaque-token", refresh="R1")
    assert auth.needs_refresh(NOW_EPOCH) is False


def test_seconds_left_for_access_and_refresh_tokens():
    auth, _ = make_auth(never, access=make_jwt(int(NOW_EPOCH + 100)), refresh=make_jwt(int(NOW_EPOCH + 5000)))
    assert auth.access_seconds_left(NOW_EPOCH) == 100
    assert auth.refresh_seconds_left(NOW_EPOCH) == 5000


@pytest.mark.parametrize("payload,expected", [
    ({"status": 0}, None),
    ({"status": -302, "data": {"domains": {"api": "api-euc1.plaud.ai"}}}, "api-euc1.plaud.ai"),
])
def test_redirect_host(payload, expected):
    assert redirect_host(payload) == expected


def test_db_token_store_roundtrip_and_overwrite(session_factory):
    store = DbTokenStore(session_factory)
    assert store.load() is None
    store.save(StoredTokens("A1", "R1", "fp1"))
    store.save(StoredTokens("A2", None, "fp2"))
    assert store.load() == StoredTokens("A2", None, "fp2")
    with session_factory() as s:
        assert len(s.scalars(select(PlaudSession)).all()) == 1
