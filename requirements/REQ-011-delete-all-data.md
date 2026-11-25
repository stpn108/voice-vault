# REQ-011: A button that deletes all data, after asking once more

| | |
|---|---|
| **Status** | APPROVED (owner's request in chat, 2026-11-25, developer mode; the scope was chosen by the owner in two questions, see "Open questions") |
| **Date** | 2026-11-25 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — (branch `claude/req-011-delete-all-data`) |
| **Related decisions** | D-009, D-020, D-034, D-079 |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

> Bau mir einen Button - mit Rückfrage - zum Löschen aller Daten

## Goal

The owner can empty the app in one go: no recording content, no tasks, topics, people, overviews, mind maps or glossary are left. Before anything is deleted he sees what goes and confirms. The Plaud login stays, so the import goes on with the next recordings.

## Acceptance criteria

1. **A tile in the administration.**
   Given `/admin`, then there is a tile "Alle Daten löschen" leading to `/admin/wipe`.
2. **The confirmation page asks and deletes nothing.**
   Given 2 kept recordings, 1 task, 1 topic, 1 person profile, 1 daily overview and 1 glossary term, when the owner opens `/admin/wipe`, then the page lists these numbers, says what stays, and has a button "Ja, alles endgültig löschen" and "Abbrechen"; all rows are still there.
3. **The button discards every recording like "Verwerfen".**
   Given the page above, when the owner presses the button, then both recordings have `discarded_at` set, an empty title and summary and no segments; the rows themselves stay, so the import does not fetch them again.
4. **The button deletes everything built from the recordings.**
   Given one row in each of `todos`, `todo_links`, `todo_events`, `topics`, `topic_notes`, `person_profiles`, `person_topic_notes`, `person_topic_hidden`, `excluded_people`, `mindmap_nodes`, `person_map_nodes`, `digests`, `text_versions`, `glossary_terms`, `glossary_aliases`, `glossary_suggestions`, when the owner presses the button, then each of these tables is empty.
5. **Login and logs stay.**
   Given a Plaud token pair, an MCP OAuth token, a sync request, an analysis run and an LLM trace, when the owner presses the button, then all of them are still there.
6. **Afterwards the page says so.**
   Given the wipe is done, then the owner lands on `/admin/wipe`, which says that no data is left and has no button.
7. **A stale page deletes nothing.**
   Given the owner opened the page with 1 task, and a second task was added before he pressed the button, then nothing is deleted, the page shows again with the new numbers and a notice (409).
8. **Only from the app's own page.**
   Given a POST without the CSRF token or with a wrong one, then it is refused (403) and nothing is deleted.

## Out of scope

- Deleting anything at Plaud beyond what "Verwerfen" already does (D-009).
- Deleting the Plaud token, the MCP OAuth tokens, `sync_requests`, `analysis_runs`, `llm_traces`.
- Deleting the database backups in `volumes/backups/`; they keep the data until they age out (`BACKUP_RETENTION_DAYS`).
- An undo.

## Success metric

No number of its own. After a wipe: `select count(*) from recordings where discarded_at is null` is 0, and the tables of criterion 4 have no rows.

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-11-25 | Delete the recording rows outright or discard them like "Verwerfen"? | Discard like "Verwerfen" (owner). |
| 2026-11-25 | Besides recordings, tasks, topics, people, agreements, overviews and mind maps: also glossary, people settings, analysis log? | Glossary and people settings yes, analysis log no (owner). |
| 2026-11-25 | How is the confirmation asked? | A separate page with the numbers and one button, like every other delete in the app (Claude's choice, D-079). |

## Tests

<Filled in at IMPLEMENTED: test file and test names per criterion.>
