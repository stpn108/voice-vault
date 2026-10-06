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
| Mindestalter | Minimum age of a recording before it may be trashed, default 15 minutes. | `MIN_AGE_MINUTES` | Measured from the end of the recording (`start_time + duration`). |

## Units and formats

| Quantity | Unit | Stored as | Displayed as |
|----------|------|-----------|--------------|
| Duration | milliseconds as delivered by Plaud | `Integer` | `H:MM:SS` |
| Dates | local day (04:00 boundary, see `code-patterns.md`) | `YYYY-MM-DD` string or `Date` | `DD.MM.YYYY` (DE) |
| Timestamps | UTC | `DateTime(timezone=True)` | local time |
