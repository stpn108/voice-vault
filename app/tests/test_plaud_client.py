"""Plaud client: endpoints, processing status, auth, retry and redirect rules (D-001)."""
import json

import httpx
import pytest

from plaud_auth import PlaudAuth
from plaud_client import (
    PlaudAuthError, PlaudClient, PlaudError, is_plaud_host, token_expiry,
)
from tests.helpers import MemoryStore, make_jwt, refresh_response

NOW_EPOCH = 1_800_000_000


def make_client(handler, token="t", refresh=""):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    auth = PlaudAuth(MemoryStore(), http, token, refresh)
    return PlaudClient(auth, "https://api-euc1.plaud.ai", http=http, sleep=lambda s: None)


def ok(data=None, **extra):
    return httpx.Response(200, json={"status": 0, **({"data": data} if data is not None else {}), **extra})


def detail_raw(trans=1, summ=1):
    return {
        "file_name": "Team Sync", "start_time": 1_760_000_000_000, "duration": 60_000,
        "content_list": [
            {"data_type": "transaction", "task_status": trans, "data_link": "https://s3.example/t.json"},
            {"data_type": "auto_sum_note", "task_status": summ, "data_id": "s1",
             "data_link": "https://s3.example/s.json"},
        ],
    }


@pytest.mark.parametrize("trans,summ,expected", [
    (1, 1, True), (0, 1, False), (1, 0, False), (2, 1, False), (1, 2, False),
])
def test_is_processed_requires_both_tasks_done(trans, summ, expected):
    assert PlaudClient.is_processed(detail_raw(trans, summ)) is expected


def test_is_processed_false_without_transcript_link():
    raw = detail_raw()
    del raw["content_list"][0]["data_link"]
    assert PlaudClient.is_processed(raw) is False


def test_is_processed_false_when_summary_item_missing():
    raw = detail_raw()
    raw["content_list"].pop()
    assert PlaudClient.is_processed(raw) is False


def test_list_recordings_pages_and_skips_trash():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        skip = int(request.url.params["skip"])
        if skip == 0:
            items = [{"id": f"r{i}", "filename": "x", "start_time": 1, "duration": 2} for i in range(50)]
        else:
            items = [{"id": "last", "is_trash": True}, {"id": "keep", "filename": "k"}]
        return ok(data_file_list=items)

    recs = make_client(handler).list_recordings()
    assert len(recs) == 51 and recs[-1].plaud_id == "keep"
    assert [c["skip"] for c in calls] == ["0", "50"]
    assert all(c["is_trash"] == "0" for c in calls)


def test_requests_carry_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return ok(data_file_list=[])

    make_client(handler, token="abc").list_recordings()
    assert seen["auth"] == "Bearer abc"


def test_http_401_raises_auth_error():
    client = make_client(lambda r: httpx.Response(401, json={}))
    with pytest.raises(PlaudAuthError):
        client.list_recordings()


def test_missing_token_raises_auth_error_without_request():
    def handler(request):
        raise AssertionError("no request expected")
    with pytest.raises(PlaudAuthError):
        make_client(handler, token="").list_recordings()


def test_get_is_retried_on_5xx_then_succeeds():
    codes = iter([503, 503, 200])

    def handler(request):
        code = next(codes)
        return httpx.Response(code) if code != 200 else ok(data_file_list=[])

    assert make_client(handler).list_recordings() == []


def test_get_gives_up_after_three_attempts():
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(500)

    with pytest.raises(PlaudError):
        make_client(handler).list_recordings()
    assert len(count) == 3


@pytest.mark.parametrize("call", ["trash", "delete_permanently"])
def test_mutations_are_never_retried(call):
    count = []

    def handler(request):
        count.append(request.method)
        return httpx.Response(503)

    with pytest.raises(PlaudError):
        getattr(make_client(handler), call)(["a"])
    assert len(count) == 1


def test_trash_and_delete_use_expected_endpoints_and_body():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return ok()

    client = make_client(handler)
    client.trash(["a", "b"])
    client.delete_permanently(["a"])
    assert seen == [("POST", "/file/trash/", ["a", "b"]), ("DELETE", "/file/", ["a"])]


@pytest.mark.parametrize("call", ["trash", "delete_permanently"])
def test_mutation_with_no_ids_is_rejected(call):
    with pytest.raises(ValueError):
        getattr(make_client(lambda r: ok()), call)([])


def test_envelope_error_status_raises():
    client = make_client(lambda r: httpx.Response(200, json={"status": 5, "msg": "nope"}))
    with pytest.raises(PlaudError, match="nope"):
        client.trash(["a"])


def test_region_redirect_is_followed_once_to_plaud_host():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "api-euc1.plaud.ai":
            return httpx.Response(200, json={"status": -302, "data": {"domains": {"api": "api-apse1.plaud.ai"}}})
        return ok(data_file_list=[])

    client = make_client(handler)
    client.list_recordings()
    assert hosts == ["api-euc1.plaud.ai", "api-apse1.plaud.ai"]
    assert client.base_url == "https://api-apse1.plaud.ai"


@pytest.mark.parametrize("host", ["evil.example.com", "plaud.ai.evil.com", "", "x.plaud.ai.attacker.net"])
def test_region_redirect_to_foreign_host_is_refused(host):
    client = make_client(
        lambda r: httpx.Response(200, json={"status": -302, "data": {"domains": {"api": host}}})
    )
    with pytest.raises(PlaudError):
        client.list_recordings()
    assert client.base_url == "https://api-euc1.plaud.ai"


def test_redirect_loop_raises():
    client = make_client(
        lambda r: httpx.Response(200, json={"status": -302, "data": {"domains": {"api": "api-euc1.plaud.ai"}}})
    )
    with pytest.raises(PlaudError, match="loop"):
        client.list_recordings()


@pytest.mark.parametrize("host,expected", [
    ("api-euc1.plaud.ai", True), ("api.plaud.ai", True), ("plaud.ai", False),
    ("a..plaud.ai", False), ("evil.com", False), ("päud.plaud.ai", False),
])
def test_is_plaud_host(host, expected):
    assert is_plaud_host(host) is expected


@pytest.mark.parametrize("token,expected", [
    (make_jwt(1_800_000_000), 1_800_000_000), ("garbage", None), ("a.b.c", None), ("", None),
])
def test_token_expiry(token, expected):
    assert token_expiry(token) == expected


def test_fetch_segments_reads_list_and_dict_shapes():
    segs = [{"speaker": "A", "content": "hi", "start_time": 0, "end_time": 5}]
    for body in (segs, {"trans_result": segs}):
        def handler(request, body=body):
            if request.url.host == "s3.example":
                return httpx.Response(200, json=body)
            return ok(detail_raw())
        client = make_client(handler)
        assert client.fetch_segments(client.get_detail("r1")) == segs


def test_download_sends_no_auth_header():
    seen = {}

    def handler(request):
        if request.url.host == "s3.example":
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=[])
        return ok(detail_raw())

    client = make_client(handler)
    client.fetch_segments(client.get_detail("r1"))
    assert seen["auth"] is None


def test_download_refuses_non_https_link():
    raw = detail_raw()
    raw["content_list"][0]["data_link"] = "http://s3.example/t.json"
    client = make_client(lambda r: ok(raw))
    with pytest.raises(PlaudError, match="https"):
        client.fetch_segments(client.get_detail("r1"))


@pytest.mark.parametrize("body,expected", [
    ("## Plain markdown", "## Plain markdown"),
    ({"ai_content": "from ai_content"}, "from ai_content"),
    ({"markdown": "from markdown"}, "from markdown"),
    ({"content": {"markdown": "nested"}}, "nested"),
    (json.dumps({"markdown": "json string"}), "json string"),
])
def test_fetch_summary_variants_from_link(body, expected):
    def handler(request):
        if request.url.host == "s3.example":
            return httpx.Response(200, json=body)
        return ok(detail_raw())
    client = make_client(handler)
    assert client.fetch_summary(client.get_detail("r1")) == expected


def test_fetch_summary_prefers_inline_content():
    raw = detail_raw()
    raw["pre_download_content_list"] = [
        {"data_id": "s1", "data_type": "auto_sum_note", "data_content": json.dumps({"ai_content": "inline"})}
    ]
    client = make_client(lambda r: ok(raw))
    assert client.fetch_summary(client.get_detail("r1")) == "inline"


# --- automatic renewal (REQ-004) -------------------------------------------
def refreshing_handler(log, new_access="NEW", fail_files_first=1):
    """Plaud stub: /file/* answers 401 until the token was refreshed once."""
    state = {"refreshed": False, "rejections": 0}

    def handler(request):
        if request.url.path == "/auth/refresh-user-token":
            state["refreshed"] = True
            log.append("refresh")
            return refresh_response(new_access, "R2")
        log.append(request.headers["authorization"])
        if not state["refreshed"] and state["rejections"] < fail_files_first:
            state["rejections"] += 1
            return httpx.Response(401)
        return ok(data_file_list=[])
    return handler


def test_req_004_401_triggers_one_refresh_and_the_call_is_retried_with_the_new_token():
    log = []
    client = make_client(refreshing_handler(log), token="OLD", refresh="R1")
    assert client.list_recordings() == []
    assert log == ["Bearer OLD", "refresh", "Bearer NEW"]


def test_req_004_mutation_is_resent_once_after_a_401_refresh():
    seen = []

    def handler(request):
        if request.url.path == "/auth/refresh-user-token":
            return refresh_response("NEW", "R2")
        seen.append((request.method, request.headers["authorization"]))
        return httpx.Response(401) if len(seen) == 1 else ok()

    make_client(handler, token="OLD", refresh="R1").trash(["a"])
    assert seen == [("POST", "Bearer OLD"), ("POST", "Bearer NEW")]


def test_second_401_after_refresh_raises_instead_of_looping():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/auth/refresh-user-token":
            return refresh_response("NEW")
        return httpx.Response(401)

    with pytest.raises(PlaudAuthError):
        make_client(handler, token="OLD", refresh="R1").list_recordings()
    assert calls.count("/auth/refresh-user-token") == 1


def test_401_without_refresh_token_raises_auth_error_without_refresh_call():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401)

    with pytest.raises(PlaudAuthError):
        make_client(handler, token="OLD").list_recordings()
    assert calls == ["/file/simple/web"]


def test_ensure_fresh_token_refreshes_when_due():
    log = []
    near_expiry = make_jwt(NOW_EPOCH + 600, iat=NOW_EPOCH - 86400 + 600)
    client = make_client(refreshing_handler(log, new_access=make_jwt(NOW_EPOCH + 86400)),
                         token=near_expiry, refresh="R1")
    client.ensure_fresh_token(NOW_EPOCH)
    assert log == ["refresh"]


def test_ensure_fresh_token_does_nothing_when_not_due():
    fresh = make_jwt(NOW_EPOCH + 86400, iat=NOW_EPOCH)
    client = make_client(lambda r: (_ for _ in ()).throw(AssertionError("no call")), token=fresh, refresh="R1")
    client.ensure_fresh_token(NOW_EPOCH)


def test_ensure_fresh_token_keeps_using_valid_token_when_refresh_has_a_transient_error():
    due = make_jwt(NOW_EPOCH + 600, iat=NOW_EPOCH - 86400 + 600)
    client = make_client(lambda r: httpx.Response(503), token=due, refresh="R1")
    client.ensure_fresh_token(NOW_EPOCH)  # no exception: the token is still valid for 10 minutes


def test_ensure_fresh_token_raises_when_refresh_fails_and_token_is_expired():
    expired = make_jwt(NOW_EPOCH - 10, iat=NOW_EPOCH - 86400)
    client = make_client(lambda r: httpx.Response(503), token=expired, refresh="R1")
    with pytest.raises(PlaudAuthError):
        client.ensure_fresh_token(NOW_EPOCH)


def test_ensure_fresh_token_raises_when_refresh_token_is_rejected():
    due = make_jwt(NOW_EPOCH + 600, iat=NOW_EPOCH - 86400 + 600)
    client = make_client(lambda r: httpx.Response(401), token=due, refresh="R1")
    with pytest.raises(PlaudAuthError):
        client.ensure_fresh_token(NOW_EPOCH)


def test_ensure_fresh_token_raises_without_any_token():
    with pytest.raises(PlaudAuthError, match="no Plaud token"):
        make_client(lambda r: ok(), token="").ensure_fresh_token(NOW_EPOCH)


def test_credential_seconds_left_prefers_the_refresh_token():
    client = make_client(never_called, token=make_jwt(NOW_EPOCH + 100), refresh=make_jwt(NOW_EPOCH + 9000))
    assert client.credential_seconds_left(NOW_EPOCH) == 9000


def test_credential_seconds_left_falls_back_to_the_access_token():
    client = make_client(never_called, token=make_jwt(NOW_EPOCH + 100))
    assert client.credential_seconds_left(NOW_EPOCH) == 100


def never_called(request):
    raise AssertionError("no request expected")
