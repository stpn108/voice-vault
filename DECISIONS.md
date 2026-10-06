# Architecture Decision Log

All architectural and design decisions are documented here.
Referenced from `CLAUDE.md` — Claude Code must know and maintain this log.

---

## How This Log Works

- **New decision?** → Add an entry **immediately**, in the same commit as the change.
- **IDs**: `D-NNN`, sequential. Next id = highest existing + 1. Never reuse,
  never renumber, never `D-XXX`. Check with `grep -o '^### D-[0-9]*' DECISIONS.md | sort | tail -1`.
- **Status FINAL** → Do not change without explicit user request.
- **Status TENTATIVE** → Can be revised if new insights emerge.
- **Superseding**: never edit a FINAL decision's content. Add a new one and
  set the old one's status to `SUPERSEDED by D-NNN`.
- **Before changing behaviour**: grep this file for the affected area. A
  decision you did not read still binds you.
- **Size**: when this file passes ~100 KB, move `SUPERSEDED` and `DEPRECATED`
  entries to `DECISIONS-ARCHIVE.md` (same format, ids unchanged). Claude
  cannot reliably read a 300 KB log every session.
- **Language**: English (`CLAUDE.md` Rule 1).

### Entry format

```markdown
### D-NNN: <Title> (FINAL | TENTATIVE)

| | |
|---|---|
| **Date** | YYYY-MM-DD |
| **Decision** | What was decided, precisely enough to implement from. |
| **In plain words** | One or two sentences the owner can read without technical background. |
| **Reasoning** | Why. Include the bug, measurement or constraint that triggered it. |
| **Rejected alternatives** | (A) … — why not; (B) … — why not |
| **Status** | **FINAL** / **TENTATIVE** / SUPERSEDED by D-NNN |
```

---

## Decisions

<!-- Add new decisions below, starting with D-001 -->

### D-001: Own minimal Plaud client, no plaud-tools dependency (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | `plaud_client.py` implements only list, detail (transcript and summary links), trash and permanent delete against the unofficial Plaud web API, based on reading the plaud-tools and plaud-api source. Auth is the bearer JWT from web.plaud.ai in `.env`; the client follows one `-302` region redirect and only to `*.plaud.ai` hosts. Only GETs are retried. |
| **In plain words** | We write our own small connector to Plaud instead of installing a third-party tool, so we control exactly what talks to your account. |
| **Reasoning** | The unofficial API holds the account token; fewer dependencies mean a smaller attack surface. Only four calls are needed. |
| **Rejected alternatives** | (A) plaud-tools as dependency — large, GUI and MCP code we do not need, owner forbade it; (B) official Plaud MCP/API — does not cover trash and delete. |
| **Status** | **FINAL** |

### D-002: Google Drive via Drive API v3 with scope drive.file (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | Upload with `google-api-python-client` and an OAuth2 refresh token in `.env` (same pattern as google-contacts-sync). Scope `https://www.googleapis.com/auth/drive.file`; the app creates the base folder itself. Files carry `appProperties` (plaud id, content hash) so updates are idempotent. The OAuth client must be set to "In production", otherwise refresh tokens expire after 7 days. |
| **In plain words** | The tool can only see the files it created itself in your Drive, nothing else. |
| **Reasoning** | Least privilege, no Drive desktop client needed on the server. |
| **Rejected alternatives** | (A) scope `drive` — sees the whole Drive; (B) service account — no storage quota on a private Gmail; (C) rclone or mounted Drive — extra moving parts. |
| **Status** | SUPERSEDED by D-004 |

### D-003: Deletion is guarded by verification, stability window and shadow mode (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | A recording is trashed only if Plaud reports transcript and summary complete (`task_status == 1`), the minimum age has passed, the Drive export is read back and matches (hash, segment count), and the content hash was stable for `STABILITY_MINUTES`. Permanent delete follows after a configurable wait and only for verified exports. Until `PLAUD_DELETE_ENABLED=true`, deletions are only logged. Doubt means no deletion. |
| **In plain words** | Nothing is deleted at Plaud unless the copy in Drive has been checked, and at first the tool only pretends to delete. |
| **Reasoning** | Deletion cannot be undone after the second step; Plaud gives no version field to detect later edits, so a content hash and a stability window are used. |
| **Rejected alternatives** | (A) delete right after upload — loses later summary changes; (B) trust `is_trans`/`is_summary` list flags alone — detail status is more reliable. |
| **Status** | **FINAL** |

### D-004: Recordings are stored in the local database, Drive dropped for v0.1 (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | Transcript segments, summary and metadata are stored in the project's PostgreSQL (tables `recordings`, `segments`). Google Drive is not used in v0.1; D-002 is superseded. Where D-003 says the Drive export is read back, read: the stored rows are read back in a new transaction and compared by content hash and segment count. A simple web UI (FastAPI, REQ-002) and an MCP server for Claude (REQ-003) read from the same tables. The data stays on the owner's server. Before `PLAUD_DELETE_ENABLED=true` a backup path for the database must be decided, because after permanent deletion at Plaud the database holds the only copy. |
| **In plain words** | The recordings now live in your own database on your server instead of in Google Drive, and you look at them in your own small web page. |
| **Reasoning** | Owner wants raw data only on his own system, a UI to view and clean up, and later Claude access via his own API. Removes the Google dependency and OAuth token handling. |
| **Rejected alternatives** | (A) Drive as primary — owner decided against it; (B) Drive and database in parallel — double the code and a second place for sensitive content; (C) files on disk only — no search or cleanup UI without more code. |
| **Status** | **FINAL** |

### D-005: Automatic token renewal with the Plaud refresh token, pair kept in the database (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | `plaud_auth.py` renews the access token with `POST /auth/refresh-user-token` (header `Cookie: pld_urt=<refresh token>`, browser-like `Origin`/`Referer`, `app-platform: web`). The answer sets the new access token as cookie `pld_ut` (a clearing `pld_ut=""` may come first, the last non-empty value counts) and may rotate `pld_urt`. The current pair is stored in `plaud_sessions` (one row). `PLAUD_TOKEN` and `PLAUD_REFRESH_TOKEN` in `.env` only seed it; a changed seed (fingerprint) replaces the stored pair, an unchanged one does not. Renewal happens ahead of expiry (when less than 25 percent of the token's lifetime, at least 15 minutes, is left) at the start of every cycle and once after any HTTP 401, after which the call is sent again once. A rejected refresh token stops the cycle, nothing is deleted, the owner is warned (log, mail if SMTP is set). The refresh token is read fresh from the database before each renewal, in case another process rotated it. Expiry warnings use the refresh token's `exp` when readable. The region redirect applies to the refresh call too, only to `*.plaud.ai`. |
| **In plain words** | The background process keeps its Plaud login alive by itself. You paste the two tokens once and only again if Plaud ever invalidates them. |
| **Reasoning** | The owner wants no manual token handling and Plaud's access token is short-lived. The endpoint and cookie names were taken from the source of Plaud-Sync (`src-tauri/src/plaud/auth.rs`); they are not documented by Plaud and not yet confirmed against the live service. Rotation means a persisted pair; the environment cannot be rewritten by a container. |
| **Rejected alternatives** | (A) E-mail and password login (`POST /auth/access-token`): stores the account password, and a password login creates a new session that evicts older ones, such as the phone app (noted in plaud-toolkit); (B) renewal by hand every few days: rejected by the owner; (C) official Plaud OAuth API: early beta with a waitlist, documented endpoints cover list and detail only; (D) keep the pair only in `.env`: lost on rotation. |
| **Correction to D-001** | D-001 rejected the official API with "does not cover trash and delete". That was not verified. The hosted Plaud MCP has `delete_recording`, which moves a recording to the trash. A permanent delete was not found there. The decision for an own client on the web API stands because only the web API offers the permanent delete; the stated reason was too strong. |
| **Status** | **FINAL** |

### D-006: Minimum age before trashing is one day; unknown duration blocks deletion (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | `MIN_AGE_MINUTES` defaults to 1440 (one day), measured from the end of the recording (start plus duration). A recording whose duration is 0 or negative is never trashed (`duration_unknown`). A re-import with unchanged content still updates start, end and duration, without restarting the stability window. The wait before the permanent delete stays at `PERMANENT_DELETE_AFTER_HOURS=24`, so a recording stays at Plaud for at least about two days. This changes the default of 15 minutes named in the first request; the variable stays configurable. |
| **In plain words** | Nothing is deleted at Plaud before it is a day old, and never when Plaud has not reported how long the recording is. |
| **Reasoning** | Owner decision. A longer wait leaves room for Plaud to finish or change summaries and for the owner to notice problems. Plaud may list a recording before its length is known, and then the age calculation would be wrong. |
| **Rejected alternatives** | (A) keep 15 minutes: owner chose one day; (B) one day for the permanent delete only: the trash step is already reversible, the age gate protects against premature deletion of unfinished recordings; (C) block on duration only inside the import: the guard belongs where the decision is made. |
| **Status** | **FINAL** |

### D-007: Database backups via Ofelia labels on the db service, no db-backup container (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | Same as template decision T-010 (`.claude/template-decisions.md`): the `db-backup` service is removed; labels on `db` define an Ofelia job `backup-<COMPOSE_PROJECT_NAME>` that runs `scripts/db-backup.sh` inside `db` on `BACKUP_SCHEDULE`. voice-vault's default project name is `voice-vault`; the job name is therefore `backup-voice-vault` unless `.env` sets another `COMPOSE_PROJECT_NAME`. |
| **In plain words** | The host's shared scheduler makes the database backups, so there is no backup container in this project. |
| **Reasoning** | Owner decision; callisto-services already runs Ofelia. Matters here more than in a plain template because the database holds the only copy of the recordings after the permanent delete at Plaud (D-004). |
| **Rejected alternatives** | See T-010. |
| **Status** | **FINAL** |

