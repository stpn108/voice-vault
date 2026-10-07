# REQ-003: Read-only MCP access for Claude

| | |
|---|---|
| **Status** | APPROVED (owner's explicit ask in chat, developer mode); the OAuth part is not started, see open questions |
| **Date** | 2026-10-07 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/festive-bohr-31plpp`, not merged) |
| **Related decisions** | D-004, D-011 |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

> Und wir brauchen jetzt noch eine API, um via MCP aus Claude heraus auf meinen Server zugreifen zu können. Das heißt, wir machen einen dedizierten Webservice mit einer Authentifizierung, den ich per Reverse Proxy dann anbinde, sprich nicht den gleichen Port verwenden, der jetzt bereits für das Webinterface verwendet wurde, sondern einen dedizierten neuen Port, ich weiß nicht, ob du da einen neuen Container bauen willst, ähm, der, genau, den ich halt eben unter einer externen URL für Claude zugänglich machen will.

## Goal

Claude (Claude Code, and claude.ai where the connector can send a header) reads the stored recordings through an MCP server on its own port and container. The server sits behind the owner's reverse proxy under an external URL and answers only requests that carry the owner's secret token.

## Acceptance criteria

1. **Own service and port.**
   Given `COMPOSE_PROFILES=mcp`, when the stack starts, then a service `mcp` listens on `127.0.0.1:${PORTS_PREFIX}020`, separate from the UI port. Without the profile the service does not exist.
2. **No token, no server.**
   Given `MCP_TOKENS` is empty or contains a token shorter than 32 characters, then the service refuses to start.
3. **Every request is authenticated.**
   Given a request without `Authorization: Bearer <token>` or with a wrong token (also a prefix or an extension of the right one), then it gets HTTP 401 and the database is not touched. A token is compared in constant time. Two tokens may be configured to rotate without downtime.
4. **Only the expected host and origin.**
   Given a `Host` header other than `localhost`, `127.0.0.1` or `MCP_ALLOWED_HOSTS`, then HTTP 400. Given an `Origin` header that is not in `MCP_ALLOWED_ORIGINS`, then HTTP 403. Requests without `Origin` (Claude's servers, CLI clients) pass.
5. **MCP over HTTP.**
   Given the official MCP client, when it connects to `/mcp`, then initialize, tools/list and tools/call work (stateless Streamable HTTP, JSON responses); GET and DELETE on `/mcp` answer 405; notifications answer 202. Oversized bodies (above 64 KB) get 413.
6. **Search and list.**
   Given stored recordings, when Claude calls `list_recordings` with a query, optional `since` and `until` (ISO date or datetime) and `limit` up to 50, then it gets id, local start, duration, state, title and the beginning of the summary, newest first, with a cursor for the next page. The query is case-insensitive over title, summary and transcript.
7. **Read one recording.**
   Given a recording id, when Claude calls `get_recording`, then it gets title, metadata, summary and the transcript as `[mm:ss] Speaker: text` lines. Long transcripts come in slices of up to 2000 segments with `next_segment_offset`. Unknown or discarded recordings return a tool error, not a protocol error.
8. **Read-only.**
   Given any request to the two recording tools, then no row is written. Both are annotated `readOnlyHint`. Discarding and deleting stay in the UI. Amended by REQ-006 and D-013: further tools write to the task tables only.
9. **Bad input is rejected cleanly.**
   Given arguments of the wrong type, out of range or unknown, then JSON-RPC error -32602; unknown methods -32601; invalid JSON 400 with -32700; internal errors return a generic message without details.
10. **No content in the log.**
    Given tool calls, then the log shows the tool, the argument names and the size of the result, never recording text, search words or the token. Failed logins are logged with the client address.

## Out of scope

- Writing, tagging, discarding or deleting through MCP.
- OAuth for claude.ai connectors: REQ-005.
- TLS and rate limiting: the owner's reverse proxy does both.
- A read-only database role for the service (possible later).

## Success metric

Claude answers a question about a stored recording using only `list_recordings` and `get_recording`. Source: the log lines `MCP tool call tool=… result_chars=…`.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-10-07 | Which Claude do you use? claude.ai accepts a fixed `Authorization` header only for organisations in a beta. | OAuth is needed (owner, 2026-10-07): see REQ-005 |
| 2026-10-07 | What do you want to do with Claude on the recordings? This decides whether more tools are needed (for example summaries across a period, action items, discarding). | open |

## Tests

Written, status stays APPROVED until merged and deployed. All in `app/tests/test_mcp.py`, plus the service queries in `test_recording_service.py`.

| Criterion | Tests |
|---|---|
| 1 | compose file checked by hand (service `mcp`, profile `mcp`, port `…020`); selection logic of `redeploy.sh` checked by hand |
| 2 | `test_req_003_server_refuses_to_start_without_a_strong_token` |
| 3 | `test_req_003_requests_without_the_exact_token_are_rejected`, `test_rejected_request_never_touches_the_database`, `test_both_tokens_work_during_a_rotation`, `test_token_is_valid` |
| 4 | `test_req_003_host_header_is_checked`, `test_req_003_origin_must_be_absent_or_allowed` |
| 5 | `test_initialize_negotiates_the_protocol_version`, `test_other_methods_on_the_endpoint_are_405`, `test_notifications_get_202_without_a_body`, `test_oversized_body_is_413`; verified once against the official MCP Python client 2.3.0 (initialize, tools/list, tools/call, error mapping) |
| 6 | `test_req_003_list_recordings_lists_newest_first_with_excerpt`, `test_req_003_search_finds_transcript_words_case_insensitively`, `test_limit_and_cursor_page_through_all_recordings`, `test_since_and_until_filter_by_start_date` |
| 7 | `test_req_003_get_recording_returns_summary_and_timestamped_transcript`, `test_long_transcripts_come_in_slices`, `test_unknown_recording_is_a_tool_error_not_a_protocol_error`, `test_discarded_recording_is_not_readable` |
| 8 | `test_req_003_tools_never_change_the_database`, `test_tools_list_names_two_read_only_tools` |
| 9 | `test_req_003_invalid_list_arguments_are_32602`, `test_req_003_invalid_get_arguments_are_32602`, `test_invalid_json_is_a_parse_error`, `test_unknown_methods_are_32601`, `test_internal_errors_do_not_leak_details` |
| 10 | `test_req_003_recording_text_never_reaches_the_log`, `test_failed_login_is_logged_without_the_token` |
