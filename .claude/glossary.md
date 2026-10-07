# Glossary

The owner's words on the left, the code's names on the right. Fill in during
onboarding, extend before introducing any new domain name. This is the
contract that keeps conversation and code from drifting apart.

## Rules

- Before naming a table, column, function or string key for a domain
  concept: look here first. Reuse the existing name.
- A new term is added **before** the code that uses it, in the same
  commit.
- One term, one code name. If two names exist, one is a bug to fix.
- Code names derive from the English column; German-speaking owners get
  the German term in the "Owner says" column only.
- Definitions state what the owner means, not what the code does.

## Terms

| Owner says | Meaning | In code | Notes |
|------------|---------|---------|-------|
| Aufnahme | A recording made with the Plaud recorder and stored in the Plaud account. | `recordings` table, `plaud_id` | Identified by the Plaud file id. |
| Import | Storing one recording (metadata, summary, segments) in the database. | `import_service.import_recording()`, `recordings`, `segments` | Audio is not part of it unless `STORE_AUDIO` is on. |
| Verifizierter Import | An import that was read back from the database: rows exist, not empty, content hash and segment count match the API response. | `recordings.verified_hash`, `recordings.verified_at` | Only a verified import allows deletion at Plaud. |
| Papierkorb | Plaud's first deletion step; the recording is hidden from the list but restorable. | `recordings.trashed_at`, `plaud_client.trash()` | Not the same as permanent deletion. |
| Endgültig löschen | Plaud's second deletion step; removes the recording from the Plaud trash. | `recordings.deleted_at`, `plaud_client.delete_permanently()` | Happens after a configurable wait, only for verified imports. |
| Fertig verarbeitet | Plaud reports transcript and AI summary as completed. | `plaud_client.is_processed()` | Both `content_list` items `transaction` and `auto_sum_note` have `task_status == 1` and a data link. Anything else means not done. |
| Zugangstoken | Short-lived Plaud user token (cookie `pld_ut`), sent as bearer on every API call. | `plaud_sessions.access_token`, `PlaudAuth.access_token()` | Roughly 24 hours (observed in other clients, to be confirmed live). Secret. |
| Refresh-Token | Long-lived Plaud token (cookie `pld_urt`) that mints a new Zugangstoken without a login. | `plaud_sessions.refresh_token`, `PlaudAuth.refresh()` | About 30 days, may rotate on use. Secret. |
| Verwerfen | Removing a recording's content (title, summary, transcript) from the database in the UI. The row stays as a tombstone so the import never fetches it again. | `recordings.discarded_at`, `recording_service.discard()` | Also makes the deletion pipeline remove the recording at Plaud (D-009). Cannot be undone. |
| Freigabe-Passwort | The password the owner types on his server's approval page when Claude connects through OAuth. | `MCP_OAUTH_PASSWORD`, `mcp_oauth_web.authorize_submit()` | Not a token: it never leaves the browser form. |
| MCP-Zugang | Door for Claude to the stored recordings: its own service `mcp`, protected by a bearer token or OAuth. | `mcp_server.py`, `mcp_tools.py`, `MCP_TOKENS` | Claude can search and read recordings and maintain tasks (D-013), never change or delete recordings or tasks. |
| Aufgabe | A task taken from the conversations, with priority 1 (urgent) to 4 (low), status open, done or dropped. | `todos`, `todo_service.py` | Created by `claude` or `owner`. Never deleted, only dropped. |
| Thema | A subject that runs through several conversations. | `topics`, `topic_notes` | Matched by name without regard to case. |
| Tagesübersicht | The daily overview Claude writes, one per day. | `digests`, `save_digest` | Saving again replaces the day. |
| Mindestalter | Minimum age of a recording before it may be trashed, default 12 hours (720 minutes, D-010). A recording with unknown (0) duration is never trashed. | `MIN_AGE_MINUTES` | Measured from the end of the recording (`start_time + duration`). |

## Units and formats

| Quantity | Unit | Stored as | Displayed as |
|----------|------|-----------|--------------|
| Duration | milliseconds as delivered by Plaud | `Integer` | `H:MM:SS` |
| Dates | local day (04:00 boundary, see `code-patterns.md`) | `YYYY-MM-DD` string or `Date` | `DD.MM.YYYY` (DE) |
| Timestamps | UTC | `DateTime(timezone=True)` | local time |
