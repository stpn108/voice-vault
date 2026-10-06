# REQ-002: Simple web UI to view, search and discard stored recordings

| | |
|---|---|
| **Status** | DRAFT |
| **Date** | 2026-10-06 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — |
| **Related decisions** | D-004 |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

> nenne UI, hätte ich insoweit schon gern, weil dann kann ich mir quasi äh, die Rohinformationen, da kann ich mir sehr sparen. Das irgendwie auf Google abzulegen. Ich kann sie in der Datenbank abspeichern, direkt auf meinem Server. Und ich könnte mir die Inhalte auch über eine einfache UI einfach mal angucken und könnte die auch aufräumen oder wegschmeißen, wenn ich irgendwas nicht mehr brauche.

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
7. **Discarding does not leave the recording at Plaud.**
   Given a discarded recording that is still at Plaud, when the next cycle runs and `PLAUD_DELETE_ENABLED=true`, then it is moved to the Plaud trash and deleted permanently after the usual wait, without the verification and stability conditions (the owner decided explicitly; the minimum age still applies). In shadow mode it is only logged.
8. **Discard needs POST with a CSRF token.**
   Given a request without a valid token, or a GET request, then nothing is discarded (HTTP 403 or 405).
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
| 2026-10-06 | Criterion 7: may a discarded recording be trashed and deleted at Plaud without a verified import? The content is then gone from both places. | open, owner decides |
| 2026-10-06 | Should the UI also offer a button to restore a discarded recording? Not possible once the content is removed; this requirement says no. | open |
| 2026-10-06 | Which `PORTS_PREFIX` is free on the server and on the laptop (the tunnel maps the same port numbers)? | open, owner chooses at deploy |

## Tests

Written at implementation.
