"""Web UI (REQ-002): rendering, escaping, host check, CSRF, discard flows."""
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import webapp
from database import Recording, Segment
from tests.helpers import add_recording

NOW = dt.datetime.now(dt.timezone.utc)  # only used to seed data relative to "now"


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    webapp.app.dependency_overrides[webapp.get_session] = override
    yield TestClient(webapp.app, base_url="http://localhost")
    webapp.app.dependency_overrides.clear()


@pytest.fixture
def seed(session_factory):
    def add(**kwargs):
        with session_factory() as s:
            rec = add_recording(s, **kwargs)
            return rec.id
    return add


def token():
    return webapp.csrf_token()


def test_req_002_list_shows_rows_and_a_next_link(client, seed):
    for i in range(60):
        seed(index=i, segments=0)
    html = client.get("/").text
    assert "Title 59" in html and "Title 10" in html and "Title 9<" not in html
    next_href = html.split('href="')[-1].split('"')[0] if "cursor=" in html else ""
    assert next_href.startswith("/?cursor=")
    second = client.get(next_href.replace("&amp;", "&"))
    assert second.status_code == 200 and "Title 9" in second.text and "Title 59" not in second.text


def test_list_with_no_recordings_says_so(client):
    assert "Keine Aufnahmen gefunden." in client.get("/").text


def test_req_002_detail_shows_all_segments_in_order_with_speaker_and_time(client, seed):
    rec_id = seed(index=1, segments=42)
    html = client.get(f"/recordings/{rec_id}").text
    assert html.count('class="segment"') == 42
    assert html.index("text 1-0") < html.index("text 1-41")
    assert "Speaker 1" in html and "01:01" in html  # segment 1 starts at 61 s


def test_req_002_stored_text_is_escaped_and_markdown_is_not_rendered(client, session_factory):
    with session_factory() as s:
        rec = add_recording(s, 1, segments=0, title="<b>bold</b>", summary="**not bold** <script>alert(1)</script>")
        s.add(Segment(recording_id=rec.id, idx=0, speaker="<i>X</i>", start_ms=0, text="<img src=x onerror=alert(2)>"))
        s.commit()
        rec_id = rec.id
    html = client.get(f"/recordings/{rec_id}").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(2)&gt;" in html
    assert "<b>bold</b>" not in html and "<i>X</i>" not in html
    assert "**not bold**" in html and "<strong>not bold" not in html


def test_req_002_search_via_query_string(client, session_factory):
    with session_factory() as s:
        rec = add_recording(s, 1, segments=0)
        s.add(Segment(recording_id=rec.id, idx=0, speaker="A", start_ms=0, text="Kündigungsfrist drei Monate"))
        add_recording(s, 2)
        s.commit()
    html = client.get("/", params={"q": "kündigungsfrist"}).text
    assert "Title 1" in html and "Title 2" not in html


@pytest.mark.parametrize("host,status", [
    ("localhost", 200), ("127.0.0.1", 200), ("localhost:18010", 200),
    ("evil.example.com", 400), ("localhost.evil.com", 400), ("127.0.0.1.nip.io", 400),
])
def test_req_002_host_header_is_checked(session_factory, host, status):
    def override():
        with session_factory() as session:
            yield session
    webapp.app.dependency_overrides[webapp.get_session] = override
    try:
        response = TestClient(webapp.app, base_url="http://localhost").get("/", headers={"host": host})
    finally:
        webapp.app.dependency_overrides.clear()
    assert response.status_code == status


def test_security_headers_are_set(client):
    headers = client.get("/").headers
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_not_exposed(client, path):
    assert client.get(path).status_code == 404


def test_req_002_confirm_page_changes_nothing(client, seed, session_factory):
    rec_id = seed(index=1)
    response = client.get(f"/recordings/{rec_id}/discard")
    assert response.status_code == 200 and f'action="/recordings/{rec_id}/discard"' in response.text
    with session_factory() as s:
        assert s.get(Recording, rec_id).discarded_at is None


def test_req_002_discard_post_with_token_removes_content_and_redirects(client, seed, session_factory):
    rec_id = seed(index=1, segments=3)
    response = client.post(f"/recordings/{rec_id}/discard", data={"csrf": token()}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    with session_factory() as s:
        rec = s.get(Recording, rec_id)
        assert rec.discarded_at is not None and rec.summary == "" and s.query(Segment).count() == 0
    assert client.get(f"/recordings/{rec_id}").status_code == 404


@pytest.mark.parametrize("data", [{}, {"csrf": ""}, {"csrf": "wrong"}])
def test_req_002_discard_without_a_valid_token_is_rejected(client, seed, session_factory, data):
    rec_id = seed(index=1)
    assert client.post(f"/recordings/{rec_id}/discard", data=data).status_code == 403
    with session_factory() as s:
        assert s.get(Recording, rec_id).discarded_at is None


def test_req_002_discard_from_another_origin_is_rejected_even_with_a_token(client, seed, session_factory):
    rec_id = seed(index=1)
    response = client.post(f"/recordings/{rec_id}/discard", data={"csrf": token()},
                           headers={"origin": "http://evil.example.com"})
    assert response.status_code == 403
    with session_factory() as s:
        assert s.get(Recording, rec_id).discarded_at is None


def test_discard_from_the_same_origin_is_accepted(client, seed):
    rec_id = seed(index=1)
    response = client.post(f"/recordings/{rec_id}/discard", data={"csrf": token()},
                           headers={"origin": "http://localhost"}, follow_redirects=False)
    assert response.status_code == 303


def test_discarding_a_missing_recording_is_404(client):
    assert client.post("/recordings/999/discard", data={"csrf": token()}).status_code == 404


def test_confirm_form_carries_a_token_that_the_post_accepts(client, seed):
    rec_id = seed(index=1)
    html = client.get(f"/recordings/{rec_id}/discard").text
    form_token = html.split('name="csrf" value="')[1].split('"')[0]
    assert client.post(f"/recordings/{rec_id}/discard", data={"csrf": form_token},
                       follow_redirects=False).status_code == 303


def _fingerprint(client):
    import re
    return re.search(r'name="fingerprint" value="([0-9a-f]+)"', client.get("/cleanup", params={"days": 90}).text).group(1)


def _seed_aged(seed, old=12, young=18):
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(old + young):
        age = 100 if i < old else 10
        seed(index=i, started=now - dt.timedelta(days=age, minutes=i), segments=0)


def test_req_002_cleanup_preview_shows_the_count(client, seed):
    _seed_aged(seed)
    html = client.get("/cleanup", params={"days": 90}).text
    assert "12 Aufnahme(n)" in html and 'name="expected_count" value="12"' in html


def test_req_002_cleanup_with_the_shown_count_discards_exactly_those(client, seed, session_factory):
    _seed_aged(seed)
    response = client.post("/cleanup", data={"days": 90, "expected_count": 12, "csrf": token(),
                                             "fingerprint": _fingerprint(client)}, follow_redirects=False)
    assert response.status_code == 303
    with session_factory() as s:
        discarded = s.scalars(select(Recording).where(Recording.discarded_at.is_not(None))).all()
        assert len(discarded) == 12


def test_cleanup_with_a_changed_count_discards_nothing(client, seed, session_factory):
    _seed_aged(seed)
    response = client.post("/cleanup", data={"days": 90, "expected_count": 11, "csrf": token(),
                                             "fingerprint": _fingerprint(client)})
    assert response.status_code == 409 and "Die Anzahl hat sich geändert" in response.text
    with session_factory() as s:
        assert s.scalars(select(Recording).where(Recording.discarded_at.is_not(None))).all() == []


def test_cleanup_needs_the_token(client, seed, session_factory):
    _seed_aged(seed)
    assert client.post("/cleanup", data={"days": 90, "expected_count": 12, "fingerprint": "x"}).status_code == 403
    with session_factory() as s:
        assert s.scalars(select(Recording).where(Recording.discarded_at.is_not(None))).all() == []


@pytest.mark.parametrize("days", [0, -1, 100000, "abc"])
def test_cleanup_rejects_invalid_days(client, days):
    assert client.get("/cleanup", params={"days": days}).status_code == 400
    assert client.post("/cleanup", data={"days": days, "expected_count": 0, "csrf": token(),
                                         "fingerprint": "x"}).status_code == 400


@pytest.mark.parametrize("path", ["/", "/cleanup", "/recordings/1"])
def test_req_002_get_requests_never_change_rows(client, seed, session_factory, path):
    seed(index=1)
    client.get(path)
    with session_factory() as s:
        rec = s.get(Recording, 1)
        assert (rec.discarded_at, rec.summary, s.query(Segment).count()) == (None, "Summary 1", 3)


def test_invalid_cursor_is_a_400(client):
    assert client.get("/", params={"cursor": "garbage"}).status_code == 400


def test_req_002_list_and_search_over_5000_recordings_respond_within_one_second(client, session_factory):
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    with session_factory() as s:
        s.add_all(
            Recording(
                plaud_id=f"bulk{i}", title=f"Meeting {i}", started_at=start + dt.timedelta(minutes=i),
                ended_at=start + dt.timedelta(minutes=i + 5), duration_ms=300_000, summary=f"Summary {i}",
                segment_count=0, content_hash="h", is_plaud_processed=True, last_changed_at=start,
            )
            for i in range(5000)
        )
        s.commit()
    began = time.perf_counter()
    assert client.get("/").status_code == 200
    assert client.get("/", params={"q": "Meeting 4999"}).status_code == 200
    assert time.perf_counter() - began < 1.0


@pytest.mark.parametrize("enabled,expected,unexpected", [
    (True, "Bei Plaud wird die Aufnahme ebenfalls gelöscht", "derzeit ausgeschaltet"),
    (False, "derzeit ausgeschaltet", "ebenfalls gelöscht"),
])
def test_req_002_confirmation_tells_what_happens_at_plaud(client, seed, enabled, expected, unexpected):
    import dataclasses
    from config import load_config
    webapp.app.dependency_overrides[webapp.get_config] = lambda: dataclasses.replace(
        load_config(), delete_enabled=enabled, min_age_minutes=1440, permanent_delete_after_hours=24)
    rec_id = seed(index=1)
    for path in (f"/recordings/{rec_id}/discard", "/cleanup?days=1"):
        html = client.get(path).text
        assert expected in html and unexpected not in html
    assert "24 Stunden" in client.get(f"/recordings/{rec_id}/discard").text if enabled else True
    webapp.app.dependency_overrides.pop(webapp.get_config)


# --- findings of the security review (D-020) --------------------------------------------------------
def test_review_cleanup_with_the_same_count_but_another_set_discards_nothing(client, seed, session_factory):
    _seed_aged(seed)
    shown = _fingerprint(client)
    with session_factory() as s:  # one old recording goes, another one becomes eligible: the count stays 12
        old = s.scalars(select(Recording).where(Recording.started_at < dt.datetime.now(dt.timezone.utc)
                                                - dt.timedelta(days=90))).first()
        old.started_at = dt.datetime.now(dt.timezone.utc)
        young = s.scalars(select(Recording).where(Recording.started_at > dt.datetime.now(dt.timezone.utc)
                                                  - dt.timedelta(days=20))).all()[-1]
        young.started_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=200)
        s.commit()
    response = client.post("/cleanup", data={"days": 90, "expected_count": 12, "csrf": token(), "fingerprint": shown})
    assert response.status_code == 409
    with session_factory() as s:
        assert s.scalars(select(Recording).where(Recording.discarded_at.is_not(None))).all() == []


@pytest.mark.parametrize("url", ["/recordings/99999999999999999999999", "/todos/99999999999999999999",
                                 "/topics/99999999999999999999", "/recordings/0", "/todos/-1"])
def test_review_ids_beyond_the_column_are_a_clean_error_not_a_crash(client, url):
    assert client.get(url).status_code in (400, 404)


@pytest.mark.parametrize("url,data", [
    ("/todos/events/99999999999999999999/undo", {}), ("/recordings/99999999999999999999/discard", {}),
    ("/topics/delete", {"topic_id": "99999999999999999999", "expected_notes": 0, "expected_tasks": 0}),
])
def test_review_posts_with_huge_ids_are_a_clean_error(client, url, data):
    assert client.post(url, data={"csrf": token(), **data}).status_code in (400, 404)


@pytest.mark.parametrize("cursor", ["!!!", "MjAyNi0wMS0wMVQwMDowMDowMCswMDowMHw5OTk5OTk5OTk5OTk5OTk5OTk5OTk5",
                                    "MDAwMS0wMS0wMVQwMDowMDowMCsxNDowMHwx", "OTk5OS0xMi0zMVQyMzo1OTo1OS0xNDowMHwx"])
def test_review_odd_cursors_are_a_clean_error(client, cursor):
    assert client.get("/", params={"cursor": cursor}).status_code == 400


def test_review_an_unexpected_error_still_gets_the_security_headers(session_factory, monkeypatch):
    def override():
        with session_factory() as session:
            yield session
    webapp.app.dependency_overrides[webapp.get_session] = override
    monkeypatch.setattr(webapp.svc, "list_recordings", lambda *a, **k: 1 / 0)
    try:
        response = TestClient(webapp.app, base_url="http://localhost", raise_server_exceptions=False).get("/")
    finally:
        webapp.app.dependency_overrides.clear()
    assert response.status_code == 500 and "Internal Server Error" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY" and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers,expected", [
    ({"origin": "http://localhost"}, 303), ({"origin": "evil://localhost"}, 403), ({"origin": "https://localhost"}, 403),
    ({"origin": "http://localhost.evil.com"}, 403), ({"origin": "null"}, 403),
    ({"sec-fetch-site": "cross-site"}, 403), ({"sec-fetch-site": "same-origin"}, 303), ({}, 303),
    # browsers send `Origin: null` for form posts under some referrer policies: only same-origin counts
    ({"origin": "null", "sec-fetch-site": "same-origin"}, 303), ({"origin": "null", "sec-fetch-site": "same-site"}, 403),
    ({"origin": "null", "sec-fetch-site": "cross-site"}, 403), ({"origin": "null", "sec-fetch-site": "none"}, 403),
    ({"origin": "http://localhost:3000", "sec-fetch-site": "same-site"}, 403),
])
def test_review_the_origin_must_match_exactly_and_cross_site_requests_are_refused(client, seed, headers, expected):
    rec_id = seed(index=1)
    response = client.post(f"/recordings/{rec_id}/discard", data={"csrf": token()}, headers=headers,
                           follow_redirects=False)
    assert response.status_code == expected


def test_review_the_pages_send_a_referrer_policy_that_keeps_form_origins_intact(client):
    # With "no-referrer" browsers post `Origin: null` and every form would be refused.
    assert client.get("/").headers["referrer-policy"] == "same-origin"
