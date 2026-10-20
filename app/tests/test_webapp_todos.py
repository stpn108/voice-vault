"""Web UI for tasks, topics and overviews (REQ-006, D-013)."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import todo_service as svc
import webapp
from database import Todo, TodoEvent
from utils import now_utc
from tests.helpers import add_recording, add_recording_on


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    webapp.app.dependency_overrides[webapp.get_session] = override
    yield TestClient(webapp.app, base_url="http://localhost")
    webapp.app.dependency_overrides.clear()


@pytest.fixture
def make_todo(session_factory):
    def make(**kwargs):
        kwargs.setdefault("actor", "claude")
        with session_factory() as s:
            return svc.create_todo(s, now_utc(), **kwargs).id
    return make


def post(client, url, **data):
    return client.post(url, data={"csrf": webapp.csrf_token(), **data}, follow_redirects=False)


def test_req_006_list_is_sorted_by_priority_and_escapes_output(client, make_todo):
    make_todo(title="Low one", priority=4)
    make_todo(title="<script>alert(1)</script>", priority=1)
    html = client.get("/todos").text
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert html.index("alert(1)") < html.index("Low one")


def test_req_006_check_off_from_the_list_completes_and_logs_owner(client, make_todo, session_factory):
    todo_id = make_todo(title="Pay invoice")
    response = post(client, f"/todos/{todo_id}/status", status="done", back="list")
    assert response.status_code == 303 and response.headers["location"] == "/todos"
    assert "Pay invoice" not in client.get("/todos").text
    assert "Pay invoice" in client.get("/todos?status=done").text
    with session_factory() as s:
        event = s.scalars(select(TodoEvent).order_by(TodoEvent.id.desc())).first()
        assert (event.kind, event.actor) == ("completed", "owner")


def test_req_006_undo_restores_the_previous_state(client, make_todo, session_factory):
    todo_id = make_todo(title="Call Anna")
    post(client, f"/todos/{todo_id}/status", status="dropped")
    page = client.get(f"/todos/{todo_id}").text
    assert "/undo" in page
    with session_factory() as s:
        event_id = s.scalars(select(TodoEvent).order_by(TodoEvent.id.desc())).first().id
    assert post(client, f"/todos/events/{event_id}/undo").status_code == 303
    with session_factory() as s:
        assert s.get(Todo, todo_id).status == "open"
    assert post(client, f"/todos/events/{event_id}/undo").status_code == 400


def test_req_006_owner_adds_and_edits_a_task(client, session_factory):
    response = post(client, "/todos", title="Buy stamps", priority="2", due="2026-12-01", topic="Errands")
    todo_id = int(response.headers["location"].rsplit("/", 1)[1])
    post(client, f"/todos/{todo_id}", title="Buy stamps", detail="Post office", priority="1", due="",
         topic="Errands", status="open")
    with session_factory() as s:
        todo = s.get(Todo, todo_id)
        assert (todo.priority, todo.detail, todo.due_date, todo.created_by) == (1, "Post office", None, "owner")


@pytest.mark.parametrize("url,data", [
    ("/todos", {"title": "x"}), ("/todos/1", {"title": "x"}), ("/todos/1/status", {"status": "done"}),
    ("/todos/events/1/undo", {}),
])
def test_req_006_state_changes_need_the_csrf_token(client, make_todo, url, data):
    make_todo(title="Keep me")
    assert client.post(url, data={"csrf": "wrong", **data}).status_code == 403
    assert client.post(url, data=data).status_code == 403


@pytest.mark.parametrize("url,data", [
    ("/todos", {"title": ""}), ("/todos", {"title": "x", "priority": "9"}), ("/todos", {"title": "x", "due": "soon"}),
    ("/todos/1/status", {"status": "deleted"}),
])
def test_req_006_invalid_input_is_rejected(client, make_todo, url, data):
    make_todo(title="Keep me")
    assert post(client, url, **data).status_code in (400, 422)


@pytest.mark.parametrize("url", ["/todos/999", "/topics/999"])
def test_req_006_unknown_ids_are_404(client, url):
    assert client.get(url).status_code == 404


def test_req_006_topics_timeline_and_digests_render(client, make_todo, session_factory):
    with session_factory() as s:
        rec_id = add_recording(s, index=1).id
        add_recording_on(s, dt.date(2026, 10, 5))
    make_todo(title="Prepare budget", topic="Budget", recording_id=rec_id)
    with session_factory() as s:
        svc.add_topic_note(s, now_utc(), topic="Budget", recording_id=rec_id, note="Estimate 12k")
        svc.save_digest(s, now_utc(), dt.date(2026, 10, 5), "Today: <b>budget</b>")
    assert "Budget" in client.get("/topics").text
    from database import Topic
    with session_factory() as s:
        budget_id = s.scalar(select(Topic.id).where(Topic.name == "Budget"))
    topic = client.get(f"/topics/{budget_id}").text
    assert "Estimate 12k" in topic and "Prepare budget" in topic
    digests = client.get("/digests").text
    assert "05.10.2026" in digests and "&lt;b&gt;budget" in digests


def test_req_006_empty_pages_render(client):
    for url in ("/todos", "/topics", "/digests"):
        assert client.get(url).status_code == 200


def test_req_006_owner_excludes_and_allows_a_topic(client, make_todo, session_factory):
    make_todo(title="Family trip", topic="Family")
    assert post(client, "/topics/exclude", name="Family", excluded="1").status_code == 303
    assert "Family trip" not in client.get("/todos").text
    assert "ausgeschlossen" in client.get("/topics").text
    assert post(client, "/topics/exclude", name="NotYetThere", excluded="1").status_code == 303
    assert post(client, "/topics/exclude", name="Family", excluded="0").status_code == 303
    assert "Family trip" in client.get("/todos").text


def test_req_006_excluding_needs_the_csrf_token(client):
    assert client.post("/topics/exclude", data={"name": "X", "csrf": "bad"}).status_code == 403


def test_req_006_digests_can_be_paged_through_by_day(client, session_factory):
    with session_factory() as s:
        add_recording_on(s, "2026-10-01", "2026-10-03", "2026-10-07")
        for day, body in ((dt.date(2026, 10, 1), "first day"), (dt.date(2026, 10, 3), "third day"),
                          (dt.date(2026, 10, 7), "last day")):
            svc.save_digest(s, now_utc(), day, body)
    latest = client.get("/digests").text
    assert "last day" in latest and 'href="/digests/2026-10-03"' in latest and 'rel="next"' in latest
    assert 'href="/digests/2026-10-07"' not in latest.split('rel="next"')[0].rsplit("<a", 1)[-1]
    middle = client.get("/digests/2026-10-03").text
    assert "third day" in middle and 'href="/digests/2026-10-01"' in middle and 'href="/digests/2026-10-07"' in middle
    assert "first day" in client.get("/digests/2026-10-01").text
    assert "Title 100" in client.get("/digests/2026-10-01").text


@pytest.mark.parametrize("day", ["2026-10-02", "nonsense", "2026-13-40"])
def test_req_006_unknown_digest_days_are_404(client, day):
    assert client.get(f"/digests/{day}").status_code == 404


# --- findings of the security review (D-020) --------------------------------------------------------
def test_review_undo_cannot_put_a_task_back_into_an_excluded_topic(client, make_todo, session_factory):
    from database import TodoEvent
    task = make_todo(title="Plan trip", topic="Secret")
    post(client, f"/todos/{task}", title="Plan trip", detail="", priority="3", due="", topic="Other", status="open")
    with session_factory() as s:
        svc.set_topic_excluded(s, "Secret", True)
        event = s.scalars(select(TodoEvent).order_by(TodoEvent.id.desc())).first().id
    assert post(client, f"/todos/events/{event}/undo").status_code == 400


def test_review_earlier_versions_of_an_overview_are_shown(client, session_factory):
    with session_factory() as s:
        add_recording_on(s, "2026-10-06")
        svc.save_digest(s, now_utc(), "2026-10-06", "first version")
        svc.save_digest(s, now_utc(), "2026-10-06", "second version")
    page = client.get("/digests/2026-10-06").text
    assert "second version" in page and "first version" in page and "Frühere Fassungen dieses Tages (1)" in page


def test_review_the_owner_can_let_the_routine_read_a_recording_again(client, session_factory):
    from database import Recording
    with session_factory() as s:
        rec_id = add_recording(s, index=1).id
        svc.mark_analyzed(s, now_utc(), rec_id)
    assert "unanalyze" in client.get(f"/recordings/{rec_id}").text
    assert client.post(f"/recordings/{rec_id}/unanalyze", data={"csrf": "bad"}).status_code == 403
    assert post(client, f"/recordings/{rec_id}/unanalyze").status_code == 303
    with session_factory() as s:
        assert s.get(Recording, rec_id).analyzed_at is None
    assert "unanalyze" not in client.get(f"/recordings/{rec_id}").text
    assert post(client, "/recordings/9999/unanalyze").status_code == 404


# --- checking off without reloading the page (D-027) ----------------------------------------------------
def test_d027_a_check_off_asked_for_json_answers_json_with_the_new_counts(client, make_todo):
    first = make_todo(title="Pay invoice")
    make_todo(title="Call Anna")
    response = client.post(f"/todos/{first}/status", data={"csrf": webapp.csrf_token(), "status": "done"},
                           headers={"Accept": "application/json"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and (body["status"], body["previous"]) == ("done", "open")
    assert body["counts"]["open"] == 1 and body["counts"]["done"] == 1
    back = client.post(f"/todos/{first}/status", data={"csrf": webapp.csrf_token(), "status": body["previous"]},
                       headers={"Accept": "application/json"}).json()
    assert back["counts"]["open"] == 2


def test_d027_without_json_the_form_still_redirects(client, make_todo):
    todo = make_todo(title="Pay invoice")
    response = post(client, f"/todos/{todo}/status", status="done", back="list")
    assert response.status_code == 303 and response.headers["location"] == "/todos"


def test_d027_a_json_check_off_still_needs_the_csrf_token(client, make_todo):
    todo = make_todo(title="Pay invoice")
    response = client.post(f"/todos/{todo}/status", data={"csrf": "bad", "status": "done"},
                           headers={"Accept": "application/json"})
    assert response.status_code == 403


def test_d027_the_list_loads_the_script_from_this_server_only(client, make_todo):
    make_todo(title="Pay invoice")
    response = client.get("/todos")
    html, csp = response.text, response.headers["content-security-policy"]
    assert '<script src="/static/todos.js" defer></script>' in html and "data-quick" in html
    assert 'data-count="open"' in html and 'id="toast"' in html
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp and "unsafe-inline' ;" not in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp and "<script>" not in html


def test_d027_the_script_is_served(client):
    response = client.get("/static/todos.js")
    assert response.status_code == 200 and "javascript" in response.headers["content-type"]
    assert "requestSubmit" in response.text


# --- status in progress and the reminder in the header (D-028) ----------------------------------------
def test_d028_starting_from_the_list_keeps_the_task_in_the_open_tab(client, make_todo):
    todo = make_todo(title="Write offer")
    body = client.post(f"/todos/{todo}/status", data={"csrf": webapp.csrf_token(), "status": "in_progress"},
                       headers={"Accept": "application/json"}).json()
    assert (body["status"], body["previous"]) == ("in_progress", "open")
    assert body["counts"]["open"] == 1 and body["counts"]["in_progress"] == 1
    html = client.get("/todos").text
    assert "Write offer" in html and 'data-status="in_progress"' in html
    assert "Write offer" in client.get("/todos?status=in_progress").text


@pytest.mark.parametrize("url", ["/", "/todos", "/topics", "/digests"])
def test_d028_every_page_reminds_of_the_tasks_in_progress(client, make_todo, url):
    started = make_todo(title="Write offer")
    make_todo(title="Call Anna")
    post(client, f"/todos/{started}/status", status="in_progress")
    html = client.get(url).text
    panel = html[html.index('id="wip"'):html.index("</div>", html.index('class="wip-all"'))]
    assert "popovertarget=\"wip\"" in html and "Write offer" in panel and "Call Anna" not in panel
    assert '<script src="/static/wip.js" defer></script>' in html


def test_d029_without_tasks_in_progress_the_header_count_is_hidden_but_ready(client, make_todo):
    make_todo(title="Call Anna")
    html = client.get("/todos").text
    assert 'data-wip-count hidden' in html and 'id="wip"' in html and "wip.js" in html


def test_d029_every_change_answers_with_the_tasks_in_progress(client, make_todo):
    started = make_todo(title="Write offer")
    body = client.post(f"/todos/{started}/status", data={"csrf": webapp.csrf_token(), "status": "in_progress"},
                       headers={"Accept": "application/json"}).json()
    assert body["id"] == started and body["in_progress"] == [{"id": started, "title": "Write offer"}]
    assert body["counts"]["in_progress"] == 1
    done = client.post(f"/todos/{started}/status", data={"csrf": webapp.csrf_token(), "status": "done"},
                       headers={"Accept": "application/json"}).json()
    assert done["in_progress"] == [] and done["counts"]["in_progress"] == 0


def test_d028_the_reminder_form_returns_to_the_page_it_came_from(client, make_todo):
    todo = make_todo(title="Write offer")
    response = client.post(f"/todos/{todo}/status", follow_redirects=False,
                           data={"csrf": webapp.csrf_token(), "status": "done", "back": "page"},
                           headers={"Referer": "http://localhost/topics"})
    assert response.status_code == 303 and response.headers["location"] == "http://localhost/topics"


def test_d028_the_reminder_never_redirects_to_another_site(client, make_todo):
    todo = make_todo(title="Write offer")
    response = client.post(f"/todos/{todo}/status", follow_redirects=False,
                           data={"csrf": webapp.csrf_token(), "status": "done", "back": "page"},
                           headers={"Referer": "http://localhost.evil.example/x"})
    assert response.headers["location"] == f"/todos/{todo}"


def test_d029_the_reminder_script_never_opens_by_itself(client):
    response = client.get("/static/wip.js")
    assert response.status_code == 200 and "todo:changed" in response.text
    assert "showPopover" not in response.text and "localStorage" not in response.text


# --- people to talk to (D-030) -------------------------------------------------------------------
def test_d030_detail_offers_the_recording_speakers_and_saves_the_ticked_ones(client, make_todo, session_factory):
    from database import Segment
    with session_factory() as s:
        rec = add_recording(s, 1)
        s.add_all([Segment(recording_id=rec.id, idx=50, speaker="Jochen Rill", text="x"),
                   Segment(recording_id=rec.id, idx=51, speaker="<b>Eve</b>", text="x")])
        s.commit()
        rec_id = rec.id
    todo = make_todo(title="Discuss budget", recording_id=rec_id)
    html = client.get(f"/todos/{todo}").text
    assert 'name="people" value="Jochen Rill"' in html and "&lt;b&gt;Eve&lt;/b&gt;" in html and "<b>Eve" not in html
    response = client.post(f"/todos/{todo}", follow_redirects=False, data={
        "csrf": webapp.csrf_token(), "title": "Discuss budget", "priority": "3", "status": "open",
        "people": ["Jochen Rill"], "people_more": "Anna, , anna"})
    assert response.status_code == 303
    html = client.get("/todos").text
    assert 'class="person" href="/todos?status=open&amp;sort=priority&amp;person=Anna"' in html
    filtered = client.get("/todos?person=Jochen%20Rill").text
    assert "Discuss budget" in filtered and "Person: <strong>Jochen Rill</strong>" in filtered
    assert "Discuss budget" not in client.get("/todos?person=Eve").text


def test_d031_a_tap_on_a_tag_saves_the_people_at_once(client, make_todo):
    todo = make_todo(title="Discuss budget", people=["Jochen Rill"])
    url = f"/todos/{todo}/people"
    added = client.post(url, data={"csrf": webapp.csrf_token(), "people": ["Jochen Rill", "Anna"]},
                        headers={"Accept": "application/json"})
    assert added.json() == {"ok": True, "people": ["Anna", "Jochen Rill"]}
    removed = client.post(url, data={"csrf": webapp.csrf_token(), "people": ["Anna"]},
                          headers={"Accept": "application/json"}).json()
    assert removed["people"] == ["Anna"]
    cleared = client.post(url, data={"csrf": webapp.csrf_token()}, headers={"Accept": "application/json"}).json()
    assert cleared["people"] == []
    assert 'data-people-url="/todos/%d/people"' % todo in client.get(f"/todos/{todo}").text


@pytest.mark.parametrize("data, status", [
    ({"csrf": "bad", "people": ["Anna"]}, 403),
    ({"people": ["x" * 81]}, 400),
])
def test_d031_tag_saves_are_checked(client, make_todo, data, status):
    todo = make_todo(title="Discuss budget")
    data = {"csrf": webapp.csrf_token(), **data}
    assert client.post(f"/todos/{todo}/people", data=data, headers={"Accept": "application/json"}).status_code == status


def test_d031_the_tag_script_is_served(client):
    response = client.get("/static/people.js")
    assert response.status_code == 200 and "data-people-url" in response.text and "textContent" in response.text
    assert "insertReplacementText" in response.text  # a pick from the suggestion list is taken at once


# --- the task page saves each field when it is left (D-032) --------------------------------------
def test_d032_a_background_save_answers_json_and_keeps_the_people(client, make_todo, session_factory):
    todo = make_todo(title="Discuss budget", people=["Anna"])
    response = client.post(f"/todos/{todo}", headers={"Accept": "application/json"}, data={
        "csrf": webapp.csrf_token(), "title": "Discuss budget", "priority": "1", "status": "in_progress",
        "due": "", "topic": "Budget", "detail": "", "people": ["Anna"]})
    body = response.json()
    assert body["ok"] is True and body["status"] == "in_progress" and body["counts"]["in_progress"] == 1
    assert body["in_progress"] == [{"id": todo, "title": "Discuss budget"}]
    with session_factory() as s:
        row = svc.get_todo(s, now_utc(), todo)[0]
        assert (row.priority, row.topic, row.people) == (1, "Budget", ["Anna"])


def test_d032_the_task_page_has_no_save_button_when_scripts_run(client, make_todo):
    html = client.get(f"/todos/{make_todo(title='Discuss budget')}").text
    assert "data-autosave" in html and '<script src="/static/task.js" defer></script>' in html
    assert "<noscript><button" in html


def test_d032_the_autosave_script_is_served(client):
    response = client.get("/static/task.js")
    assert response.status_code == 200 and "reportValidity" in response.text and "todo:changed" in response.text
