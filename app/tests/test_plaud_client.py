"""Plaud client: Plaud 4.0 endpoints, processing status, auth, retry and redirect rules (D-001, D-080)."""
import json

import httpx
import pytest

from plaud_auth import PlaudAuth
from plaud_client import (
    PlaudAuthError, PlaudClient, PlaudError, is_plaud_host, token_expiry,
)
from tests.helpers import MemoryStore, make_jwt, refresh_response

NOW_EPOCH = 1_800_000_000
V4 = "/file-app/v4"


def make_client(handler, token="t", refresh=""):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    auth = PlaudAuth(MemoryStore(), http, token, refresh)
    return PlaudClient(auth, "https://api-euc1.plaud.ai", http=http, sleep=lambda s: None)


def ok(data=None, **extra):
    return httpx.Response(200, json={"status": 0, **({"data": data} if data is not None else {}), **extra})


def items(*entries, next_cursor=None):
    return ok({"items": list(entries), "next_cursor": next_cursor})


def detail_raw(trans=1, summ=1, container="on_w_abc", shown="sys_recordings"):
    """`files/detail` of a recording in its own container folder, shown in a system folder."""
    return {
        "node": {"node_id": "on_f_abc", "name": "Team Sync", "version_ms": 5,
                 "folder": {"folder_id": container}, "display_folder": {"folder_id": shown}},
        "meta": {"file_id": "of_abc", "start_time": 1_760_000_000_000, "duration": 60_000},
        "objects": [
            {"object_type": "AUDIO", "task_status": None, "content_url": "https://s3.example/a.ogg"},
            {"object_type": "TRANSCRIPT", "task_status": trans, "content_url": "https://s3.example/t.json"},
            {"object_type": "SUMMARY", "task_status": summ, "content_url": "https://s3.example/s.json"},
        ],
    }


@pytest.mark.parametrize("trans,summ,expected", [
    (1, 1, True), (None, None, True), (None, 1, True),
    (0, 1, False), (1, 0, False), (2, 1, False), (1, 2, False), (-1, 1, False),
])
def test_is_processed_requires_both_tasks_done(trans, summ, expected):
    assert PlaudClient.is_processed(detail_raw(trans, summ)) is expected


@pytest.mark.parametrize("object_type", ["TRANSCRIPT", "SUMMARY"])
def test_is_processed_false_without_a_download_link(object_type):
    raw = detail_raw()
    next(o for o in raw["objects"] if o["object_type"] == object_type).pop("content_url")
    assert PlaudClient.is_processed(raw) is False


def test_is_processed_false_when_summary_item_missing():
    raw = detail_raw()
    raw["objects"].pop()
    assert PlaudClient.is_processed(raw) is False


def test_list_recordings_follows_the_cursor_and_skips_trash():
    calls = []

    def handler(request):
        assert request.url.path == f"{V4}/recordings/all"
        calls.append(dict(request.url.params))
        if "cursor" not in request.url.params:
            first = [{"file_id": f"f_r{i}", "name": "x", "created_at_show_ms": 1, "duration_ms": 2} for i in range(50)]
            return items(*first, next_cursor="c2")
        return items({"file_id": "f_gone", "trash_info": {"trashed_at": 1}}, {"file_id": "f_keep", "name": "k"})

    recs = make_client(handler).list_recordings()
    assert len(recs) == 51 and recs[-1].plaud_id == "f_keep"
    assert (recs[0].title, recs[0].start_ms, recs[0].duration_ms) == ("x", 1, 2)
    assert [c.get("cursor") for c in calls] == [None, "c2"]
    assert all(c["sort_by"] == "created" and c["sort_order"] == "desc" for c in calls)


@pytest.mark.parametrize("response", [ok(), ok({}), ok({"items": None}), ok({"items": {"a": 1}})])
def test_d080_a_list_answer_without_items_is_an_error_not_an_empty_library(response):
    with pytest.raises(PlaudError, match="no item list"):
        make_client(lambda r: response).list_recordings()


@pytest.mark.parametrize("listed,stored", [("of_0123abcd", "0123abcd"), ("f_9f8e7d", "f_9f8e7d")])
def test_d080_a_recording_from_before_4_0_keeps_its_old_id(listed, stored):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == f"{V4}/recordings/all":
            return items({"file_id": listed, "name": "x"})
        return ok(detail_raw())

    client = make_client(handler)
    (rec,) = client.list_recordings()
    assert rec.plaud_id == stored
    client.get_detail(rec.plaud_id)
    assert paths[-1] == f"{V4}/files/detail/{listed}"


def test_d080_the_detail_takes_title_from_the_node_and_times_from_meta():
    detail = make_client(lambda r: ok(detail_raw())).get_detail("abc")
    assert (detail.title, detail.start_ms, detail.duration_ms, detail.is_processed) == (
        "Team Sync", 1_760_000_000_000, 60_000, True)


def test_requests_carry_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return items()

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
        return httpx.Response(code) if code != 200 else items()

    assert make_client(handler).list_recordings() == []


def test_get_gives_up_after_three_attempts():
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(500)

    with pytest.raises(PlaudError):
        make_client(handler).list_recordings()
    assert len(count) == 3


# --- trash and permanent delete through Plaud 4.0 nodes (D-080) -----------------------------------------
def node_plaud(writes, trash=(), failed=(), window=None, children=()):
    """Plaud 4.0 stub: detail, the container's sibling window, folder children, the trash, the node writes."""
    def handler(request):
        path, params = request.url.path, request.url.params
        if request.method == "DELETE":
            writes.append((path, json.loads(request.content)))
            return ok({"items": [], "failed": list(failed)})
        if path.startswith(f"{V4}/files/detail/"):
            return ok(detail_raw(container="on_w_" + path.rsplit("/", 1)[1].removeprefix("of_")))
        if path == f"{V4}/nodes/ancestor-items":
            own = [{"node_id": "on_w_" + params["file_id"].removeprefix("of_"), "version_ms": 7}]
            return ok({"flat_items": own if window is None else window})
        if path == f"{V4}/folders/children":
            return items(*children)
        if path == f"{V4}/folders/content":
            assert params["scope"] == "trash"
            return items(*trash)
        raise AssertionError(path)
    return handler


def test_d080_trash_and_delete_name_the_container_folder_with_its_version():
    writes = []
    client = make_client(node_plaud(writes, trash=[{"type": 1, "node_id": "on_w_a", "version_ms": 9}]))
    client.trash(["a", "b"])
    client.delete_permanently(["a"])
    assert writes == [
        (f"{V4}/nodes/trash", {"items": [{"node_id": "on_w_a", "origin_version": 7},
                                         {"node_id": "on_w_b", "origin_version": 7}]}),
        (f"{V4}/nodes/delete", {"items": [{"node_id": "on_w_a", "origin_version": 9}]}),
    ]


def test_d080_a_recording_without_a_container_is_trashed_by_its_file_node():
    writes = []
    base = node_plaud(writes)

    def handler(request):
        if request.url.path.startswith(f"{V4}/files/detail/"):
            return ok(detail_raw(container="fd_work", shown="fd_work"))
        return base(request)

    make_client(handler).trash(["a"])
    assert writes == [(f"{V4}/nodes/trash", {"items": [{"node_id": "on_f_abc", "origin_version": 5}]})]


def test_d080_a_container_outside_the_sibling_window_is_looked_up_in_its_folder():
    writes = []
    handler = node_plaud(writes, window=[{"node_id": "on_w_other", "version_ms": 1}],
                         children=[{"node_id": "on_w_x", "version_ms": 3}, {"node_id": "on_w_a", "version_ms": 8}])
    make_client(handler).trash(["a"])
    assert writes == [(f"{V4}/nodes/trash", {"items": [{"node_id": "on_w_a", "origin_version": 8}]})]


def test_d080_a_container_that_cannot_be_found_is_not_trashed():
    writes = []
    with pytest.raises(PlaudError, match="container folder"):
        make_client(node_plaud(writes, window=[])).trash(["a"])
    assert writes == []


def test_d080_a_permanent_delete_outside_the_trash_is_refused_and_sends_nothing():
    writes = []
    client = make_client(node_plaud(writes, trash=[{"type": 1, "node_id": "on_w_b", "version_ms": 9}]))
    with pytest.raises(PlaudError, match="not in the Plaud trash, not deleted: a"):
        client.delete_permanently(["a", "b"])
    assert writes == []


@pytest.mark.parametrize("call", ["trash", "delete_permanently"])
def test_d080_a_node_write_with_failed_items_raises_although_plaud_says_status_0(call):
    failed = [{"node_id": "on_w_a", "error_code": -1800313, "error_message": "node version conflict"}]
    client = make_client(node_plaud([], trash=[{"type": 1, "node_id": "on_w_a", "version_ms": 9}], failed=failed))
    with pytest.raises(PlaudError, match="failed for 1 item.*node version conflict"):
        getattr(client, call)(["a"])


@pytest.mark.parametrize("call", ["trash", "delete_permanently"])
def test_mutations_are_never_retried(call):
    writes = []
    base = node_plaud([], trash=[{"type": 1, "node_id": "on_w_a", "version_ms": 9}])

    def handler(request):
        if request.method == "DELETE":
            writes.append(request.url.path)
            return httpx.Response(503)
        return base(request)

    with pytest.raises(PlaudError):
        getattr(make_client(handler), call)(["a"])
    assert len(writes) == 1


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
        return items()

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
    raw["objects"][1]["content_url"] = "http://s3.example/t.json"
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


@pytest.mark.parametrize("summ", [0, -1])
def test_fetch_summary_is_empty_and_downloads_nothing_while_the_summary_is_not_done(summ):
    def handler(request):
        assert request.url.host != "s3.example"
        return ok(detail_raw(summ=summ))
    client = make_client(handler)
    assert client.fetch_summary(client.get_detail("r1")) == ""


# --- automatic renewal (REQ-004) -------------------------------------------
def refreshing_handler(log, new_access="NEW", fail_files_first=1):
    """Plaud stub: the file API answers 401 until the token was refreshed once."""
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
        return items()
    return handler


def test_req_004_401_triggers_one_refresh_and_the_call_is_retried_with_the_new_token():
    log = []
    client = make_client(refreshing_handler(log), token="OLD", refresh="R1")
    assert client.list_recordings() == []
    assert log == ["Bearer OLD", "refresh", "Bearer NEW"]


def test_req_004_mutation_is_resent_once_after_a_401_refresh():
    seen = []
    base = node_plaud([])

    def handler(request):
        if request.url.path == "/auth/refresh-user-token":
            return refresh_response("NEW", "R2")
        if request.method == "DELETE":
            seen.append(request.headers["authorization"])
            return httpx.Response(401) if len(seen) == 1 else ok({"items": []})
        return base(request)

    make_client(handler, token="OLD", refresh="R1").trash(["a"])
    assert seen == ["Bearer OLD", "Bearer NEW"]


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
    assert calls == [f"{V4}/recordings/all"]


def test_ensure_fresh_token_refreshes_when_due():
    log = []
    near_expiry = make_jwt(NOW_EPOCH + 600, iat=NOW_EPOCH - 86400 + 600)
    client = make_client(refreshing_handler(log, new_access=make_jwt(NOW_EPOCH + 86400)),
                         token=near_expiry, refresh="R1")
    client.ensure_fresh_token(NOW_EPOCH)
    assert log == ["refresh"]


def test_req_004_refresh_token_alone_is_enough_the_access_token_is_fetched_on_the_first_cycle():
    log = []
    new_access = make_jwt(NOW_EPOCH + 86400, iat=NOW_EPOCH)
    client = make_client(refreshing_handler(log, new_access=new_access), token="", refresh="R1")
    client.ensure_fresh_token(NOW_EPOCH)
    assert client.list_recordings() == []
    assert log == ["refresh", f"Bearer {new_access}"]


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


# --- the cleaned transcript carries the names (D-026), read from Plaud 4.0 objects (D-080) ----------------
from plaud_client import PlaudDetail  # noqa: E402

STALE = [{"speaker": "Dennis", "content": "a", "start_time": 0, "end_time": 1},
         {"speaker": "Speaker 2", "content": "b", "start_time": 1, "end_time": 2}]
CURRENT = [{"speaker": "Dennis", "original_speaker": "Speaker 1", "content": "a", "start_time": 0, "end_time": 1},
           {"speaker": "Jochen Rill", "original_speaker": "Speaker 2", "content": "b", "start_time": 1, "end_time": 2}]
POLISHED = [{"speaker": "Dennis", "content": "a.", "start_time": 0, "end_time": 1},
            {"speaker": "Jochen Rill", "content": "b.", "start_time": 1, "end_time": 2}]


def polished_detail(status=1, with_link=True, original=True):
    raw = detail_raw()
    if not original:
        raw["objects"] = [o for o in raw["objects"] if o["object_type"] != "TRANSCRIPT"]
    item = {"object_type": "POLISHED_TRANSCRIPT", "task_status": status}
    if with_link:
        item["content_url"] = "https://s3.example/p.json"
    raw["objects"].append(item)
    return PlaudDetail("abc123", "t", 1_760_000_000_000, 60_000, True, raw)


def polish_client(polished_file=None):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/p.json":
            return httpx.Response(200, json=polished_file if polished_file is not None else {"trans_result": POLISHED})
        return httpx.Response(200, json={"trans_result": STALE})
    return make_client(handler), calls


@pytest.mark.parametrize("shape", [POLISHED, {"trans_result": POLISHED}, json.dumps({"trans_result": POLISHED})])
@pytest.mark.parametrize("status", [1, None])
def test_d026_the_cleaned_transcript_is_used(shape, status):
    client, calls = polish_client(shape)
    assert [s["speaker"] for s in client.fetch_segments(polished_detail(status))] == ["Dennis", "Jochen Rill"]
    assert calls == ["/p.json"]


@pytest.mark.parametrize("detail,polished_file", [
    (polished_detail(status=0), None), (polished_detail(with_link=False), None), (polished_detail(), []),
])
def test_d026_without_a_finished_cleaned_transcript_the_original_is_used_and_logged(detail, polished_file, caplog):
    client, calls = polish_client(polished_file)
    with caplog.at_level("WARNING"):
        assert [s["speaker"] for s in client.fetch_segments(detail)] == ["Dennis", "Speaker 2"]
    assert calls[-1] == "/t.json"
    assert "Cleaned transcript not available, using the original one" in caplog.text


def test_d026_an_unfinished_cleaned_transcript_is_not_downloaded():
    client, calls = polish_client()
    client.fetch_segments(polished_detail(status=0))
    assert "/p.json" not in calls


@pytest.mark.parametrize("status", [0, 1])
def test_d026_without_any_transcript_the_import_fails_instead_of_storing_nothing(status):
    client, _ = polish_client([])
    detail = polished_detail(original=False)
    if status == 0:
        detail.raw["objects"][-1]["task_status"] = 0
    with pytest.raises(PlaudError, match="no readable transcript"):
        client.fetch_segments(detail)


def test_live_check_speaker_report_prints_labels_from_both_transcripts(capsys):
    import plaud_live_check
    raw = polished_detail().raw
    raw["meta"]["speaker_map"] = {"Speaker 2": "Jochen Rill"}

    def handler(request):
        if request.url.path.startswith(f"{V4}/files/detail/"):
            return ok(raw)
        if request.url.path == "/p.json":
            return httpx.Response(200, json={"trans_result": CURRENT})
        return httpx.Response(200, json={"trans_result": STALE})
    plaud_live_check.show_speakers(make_client(handler), "abc123")
    out = capsys.readouterr().out
    assert "POLISHED_TRANSCRIPT labels: speaker='Dennis' original='Speaker 1'" in out
    assert "\nTRANSCRIPT labels: speaker='Dennis' original=None x1; speaker='Speaker 2' original=None" in out
    assert "detail field: meta.speaker_map" in out
    assert "content': 'a'" not in out and "'b'" not in out and "s3.example" not in out


# --- the trash listing (D-042, D-080) ---------------------------------------------------------------------
def test_d042_trash_ids_are_read_from_the_trash_listing():
    opened = []

    def handler(request):
        if request.url.path == f"{V4}/folders/children":
            opened.append(request.url.params["node_id"])
            return items({"file_id": "f_note", "file_type": "MD"}, {"file_id": "f_new", "file_type": "REC"})
        assert request.url.path == f"{V4}/folders/content" and request.url.params["scope"] == "trash"
        return items(
            {"type": 1, "node_id": "on_w_abc123"},                  # container from before 4.0
            {"type": 1, "node_id": "n_c1"},                         # newer container: opened once
            {"type": 2, "node_id": "n_f2", "file_id": "of_def456"},  # a file straight in the trash
            {"type": 1, "node_id": "n_fd", "icon": "folder"},       # a folder of the owner
            {"type": 1, "node_id": "n_sys", "system_folder_type": 3},
            {"type": 2, "node_id": "n_bad", "file_id": "bad id!"},
            "junk",
        )

    assert make_client(handler).list_trash_ids() == {"abc123", "f_new", "def456"}
    assert opened == ["n_c1"]
