# REQ-001: Import Plaud recordings into the local database and delete at Plaud

| | |
|---|---|
| **Status** | APPROVED |
| **Date** | 2026-10-06 |
| **Requested by** | Dennis Winter |
| **Implemented in** | — |
| **Related decisions** | D-001, D-003, D-004 (D-002 superseded) |
| **Supersedes / superseded by** | — |

## Owner ask (verbatim)

> Mit voice-vault möchte ich ein kleines, sicherheitsbewusstes Tool bauen, das meine Plaud-Aufnahmen (Plaud.ai Voice Recorder) regelmäßig aus meinem Plaud-Account nach Google Drive exportiert und danach bei Plaud löscht. Ziel ist Datenminimierung: Gesprächsinhalte sollen so kurz wie möglich bei Plaud liegen. Weiterverarbeitet werden sie später mit Claude, nicht mit Plaud.

## Owner ask, update 2026-10-06 (verbatim)

> [...] hätte ich insoweit schon gern [eine UI], weil dann kann ich mir quasi die Rohinformationen, da kann ich mir sehr sparen, das irgendwie auf Google abzulegen. Ich kann sie in der Datenbank abspeichern, direkt auf meinem Server. Und ich könnte mir die Inhalte auch über eine einfache UI einfach mal angucken und könnte die auch aufräumen oder wegschmeißen, wenn ich irgendwas nicht mehr brauche.

## Goal

Every 10 minutes the tool fetches new Plaud recordings and stores transcript (speakers, timestamps), AI summary and metadata in the local PostgreSQL database on the owner's server. After the stored copy is read back and verified, the recording is moved to the Plaud trash and, after a wait, deleted permanently. A recording that Plaud still changes is re-imported before anything is deleted. Google Drive is not used in this requirement. The UI (REQ-002) and the Claude access via MCP (REQ-003) build on the stored data and are separate requirements.

## Acceptance criteria

1. **Stored content.**
   Given a processed recording "Team Sync" that started 2026-10-06 14:30 local time with 42 segments, when the import runs, then the database holds one recording row (plaud id, title, start, duration, summary, content hash) and 42 segment rows (speaker, start and end in ms, text).
2. **Audio is off by default.**
   Given `STORE_AUDIO` unset, when an import runs, then no audio is downloaded or stored.
3. **Trash only if all conditions hold.**
   Given a recording, when any of these is false: transcript and summary completed at Plaud, age at least `MIN_AGE_MINUTES` (default 1440 = one day since 2026-10-06, D-006; from end of recording), duration known (greater than 0), import verified (row read back from the database, not empty, hash and segment count match the API response), then it is not trashed and is checked again in the next run.
4. **Permanent delete only after the wait and only if verified.**
   Given a trashed recording with a verified import, when `PERMANENT_DELETE_AFTER_HOURS` have passed, then it is deleted permanently. Without a verified import it is never deleted.
5. **Changed summary is re-imported.**
   Given an imported recording whose summary or transcript hash changes at Plaud, when the next run sees it, then the stored rows are updated and the deletion conditions restart; deletion requires the content hash to be stable for `STABILITY_MINUTES`.
6. **Shadow mode.**
   Given `PLAUD_DELETE_ENABLED=false` (default until the first live check), when runs happen, then recordings are imported and the would-be deletions are logged, but nothing is deleted at Plaud.
7. **Token expiry.**
   Given a Plaud token with less than 5 days left, when a run starts, then a warning is logged and, if SMTP is configured, a mail is sent. Given a 401 from Plaud, then the run aborts and nothing is deleted.
8. **No overlapping runs.**
   Given a run is still active when the next interval fires, then the new run does not start.

## Out of scope

- The web UI (list, view, delete, cleanup): REQ-002, not started.
- Making the texts available to Claude (MCP server): REQ-003, not started.
- Google Drive export: dropped for now (D-004), may return as an optional backup target.
- Any processing of the texts beyond storing them.
- Several Plaud accounts.

## Success metric

Table `recordings`: share of recordings with `verified_at` set before `trashed_at`, target 100 percent; and age of the oldest recording still at Plaud (`trashed_at` null), target under about 30 minutes after processing finished. Backup of the database must exist before the first live deletion (see D-004).

## Open questions

| Date | Question | Answer |
|------|----------|--------|
| 2026-10-06 | Does `DELETE /file/` only work on trashed recordings? | open, check live with one test recording |
| 2026-10-06 | What other `task_status` values exist besides 1? | open, treated as not done |
| 2026-10-06 | Mail for token expiry via SMTP: which server? | open, log only until configured |
| 2026-10-06 | Token handling | superseded by REQ-004: tokens are renewed automatically, the manual-token note no longer applies |
| 2026-10-06 | Backup of the only copy: is the Ofelia pg_dump every 4 hours enough, or off-server copy needed? | open, decide before `PLAUD_DELETE_ENABLED=true` |

## Tests

Written, status stays APPROVED until merged and deployed.

| Criterion | Tests |
|---|---|
| 1 Stored content | `test_import_service.py::test_req_001_import_stores_recording_and_all_segments` |
| 2 Audio off | `test_config.py::test_req_001_defaults` (no audio code path exists; flag reserved) |
| 3 Trash conditions | `test_deletion_service.py::test_req_001_each_unmet_condition_blocks_trashing`, `test_recording_with_unmet_condition_is_not_trashed` |
| 4 Permanent delete | `test_req_001_permanent_delete_after_wait_for_verified_import`, `test_req_001_permanent_delete_never_without_verified_import` |
| 5 Changed summary | `test_import_service.py::test_req_001_changed_summary_is_reimported_and_unverified_until_checked` |
| 6 Shadow mode | `test_deletion_service.py::test_req_001_shadow_mode_only_logs` |
| 7 Token expiry | `test_sync_job.py::test_req_001_token_with_less_than_5_days_warns_and_mails_once_per_day`, `test_req_001_auth_error_during_import_skips_deletion` |
| 8 No overlap | `test_sync_job.py::test_req_001_scheduler_does_not_overlap_runs` |
| Client rules (D-001) | `test_plaud_client.py` |

Mail sending itself (`notify.py`) is not unit-tested against a real SMTP server.
