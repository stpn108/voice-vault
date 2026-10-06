# REQ-002: Simple web UI to view, search and discard stored recordings

| | |
|---|---|
| **Status** | APPROVED (owner asked for the UI again in chat, developer mode) |
| **Date** | 2026-10-06 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/festive-bohr-31plpp`, not merged) |
| **Related decisions** | D-004, D-008 |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

> nenne UI, hätte ich insoweit schon gern, weil dann kann ich mir quasi äh, die Rohinformationen, da kann ich mir sehr sparen. Das irgendwie auf Google abzulegen. Ich kann sie in der Datenbank abspeichern, direkt auf meinem Server. Und ich könnte mir die Inhalte auch über eine einfache UI einfach mal angucken und könnte die auch aufräumen oder wegschmeißen, wenn ich irgendwas nicht mehr brauche.

> Und mir fehlt noch die Oberfläche, von der ich gesprochen habe, die ich gerne hätte, wo ich mir angucken kann, was das eigentlich ist. was ich was hier runtergeladen wurde. Vielleicht die ganzen Inhalte haben.

> Mach dir mal keine Sorgen, wie ich an die UI rankomme. [...] ein Skript drin, was letztendlich ermöglicht, dass ich an die intern gemounteten Ports über einen SSH-Tunnel rankomme.

## Goal

The owner opens a small web page on the server (reached through his SSH tunnel), sees the recordings stored by REQ-001, reads summary and transcript, finds a recording by a word, and discards recordings he does not need any more. No login in the app: access control is the loopback-only port plus the SSH tunnel.

## Acceptance criteria

1. **List.**
   Given 120 stored recordings, when the owner opens `/`, then the 50 newest are shown (title, local date and time, duration, state) with a "next" link that continues exactly after the last row, without skips or duplicates when a new recording arrives meanwhile (keyset paging, no offset).
2. **State per recording.**
   Given recordings in different states, then each row shows one of: imported, verified, trashed at Plaud, deleted at Plaud, waiting (Plaud not finished or content still changing).
3. **Detail.**
   Given a recording with 42 segments, when the owner opens it, then the page shows title, start and duration, the summary and all 42 segments in order with speaker and `mm:ss` timestamp. All stored text is HTML-escaped; the summary is shown as plain text, Markdown is not rendered to HTML.
4. **Search.**
   Given a recording whose transcript contains "Kündigungsfrist", when the owner searches "kündigungsfrist", then it is found (case-insensitive over title, summary and segment text, parameterised query) and the matching list shows only matching recordings. An empty search shows everything.
5. **Discard one recording.**
   Given a stored recording, when the owner confirms the discard, then summary, segments and title are removed from the database, the row stays as a tombstone (`plaud_id`, `discarded_at`) and the import never fetches that `plaud_id` again.
6. **Discard older than N days.**
   Given 30 recordings of which 12 are older than 90 days, when the owner enters 90 and confirms the shown count "12", then exactly those 12 are discarded as in criterion 5.
7. **Discarding and Plaud (default until the owner decides).**
   Given a discarded recording that is still at Plaud, then the cycle does not touch it at Plaud and the import never fetches it again. Given a discarded recording that was already in the Plaud trash, then the permanent delete after the usual wait still happens, because its import had been verified before the discard. Whether a discard should also remove a still-listed recording at Plaud is an open decision (see open questions).
8. **Discard needs POST with a CSRF token.**
   Given a request without a valid token, or from another origin, then nothing is discarded (HTTP 403). A GET request only shows the confirmation page and changes nothing.
9. **Only the expected host.**
   Given a `Host` header other than `localhost`, `127.0.0.1` or the names in `UI_ALLOWED_HOSTS`, then the response is HTTP 400 (DNS rebinding guard).
10. **Read access stays side-effect free.**
    Given any GET request, then no row is changed.

## Out of scope

- Login, users, roles (loopback port plus SSH tunnel is the access control).
- Editing summaries or transcripts.
- Exports and downloads (Markdown or JSON files), Google Drive.
- Access for Claude: REQ-003 (MCP server).
- Audio playback.
- English UI strings beyond what `strings.py` offers; the UI texts are German and English via `strings.py`.

## Success metric

The list page and a search over 5,000 recordings respond in under 1 second on the server (measured in a test with generated rows and in the request log). Count of rows with `discarded_at` set shows how often the owner cleans up.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-10-06 | Criterion 7: should discarding also move a still-listed recording to the Plaud trash and delete it there after the wait (content then gone from both places)? Today it stays at Plaud untouched. | open, owner decides |
| 2026-10-06 | Should the UI also offer a button to restore a discarded recording? Not possible once the content is removed; this requirement says no. | open |
| 2026-10-06 | Which `PORTS_PREFIX` is free on the server and on the laptop (the tunnel maps the same port numbers)? | open, owner chooses at deploy |

## Tests

Written, status stays APPROVED until merged and deployed.

| Criterion | Tests |
|---|---|
| 1 List and paging | `test_recording_service.py::test_req_002_first_page_has_the_50_newest_and_a_cursor`, `test_req_002_paging_has_no_skips_or_duplicates_even_if_a_new_recording_arrives`, `test_paging_is_stable_for_recordings_with_the_same_start`; `test_webapp.py::test_req_002_list_shows_rows_and_a_next_link` |
| 2 State | `test_recording_service.py::test_req_002_state_per_recording` |
| 3 Detail and escaping | `test_webapp.py::test_req_002_detail_shows_all_segments_in_order_with_speaker_and_time`, `test_req_002_stored_text_is_escaped_and_markdown_is_not_rendered` |
| 4 Search | `test_recording_service.py::test_req_002_search_is_case_insensitive_over_transcript`, `test_search_covers_title_summary_and_segments`, `test_search_treats_wildcards_literally`, `test_search_with_sql_metacharacters_is_harmless`; `test_webapp.py::test_req_002_search_via_query_string` |
| 5 Discard one | `test_recording_service.py::test_req_002_discard_removes_content_and_keeps_a_tombstone`; `test_webapp.py::test_req_002_discard_post_with_token_removes_content_and_redirects`; `test_import_service.py::test_req_002_discarded_recording_is_never_fetched_or_stored_again` |
| 6 Discard older than N days | `test_recording_service.py::test_req_002_discard_older_than_n_days_discards_exactly_those`; `test_webapp.py::test_req_002_cleanup_with_the_shown_count_discards_exactly_those`, `test_cleanup_with_a_changed_count_discards_nothing` |
| 7 Plaud default | `test_deletion_service.py::test_req_002_discarded_recording_is_not_trashed_at_plaud_by_default`, `test_req_002_already_trashed_recording_is_still_deleted_permanently_after_a_discard` |
| 8 CSRF | `test_webapp.py::test_req_002_discard_without_a_valid_token_is_rejected`, `test_req_002_discard_from_another_origin_is_rejected_even_with_a_token`, `test_req_002_confirm_page_changes_nothing`, `test_cleanup_needs_the_token` |
| 9 Host check | `test_webapp.py::test_req_002_host_header_is_checked` |
| 10 GET is side-effect free | `test_webapp.py::test_req_002_get_requests_never_change_rows`, `test_recording_service.py::test_req_002_reading_does_not_change_rows` |
| Success metric | `test_webapp.py::test_req_002_list_and_search_over_5000_recordings_respond_within_one_second` (SQLite; measure on the server with the request log) |
