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
| **Status** | **FINAL**; the default values are amended by D-010, the duration guard stands |

### D-007: Database backups via Ofelia labels on the db service, no db-backup container (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | Same as template decision T-010 (`.claude/template-decisions.md`): the `db-backup` service is removed; labels on `db` define an Ofelia job `backup-<COMPOSE_PROJECT_NAME>` that runs `scripts/db-backup.sh` inside `db` on `BACKUP_SCHEDULE`. voice-vault's default project name is `voice-vault`; the job name is therefore `backup-voice-vault` unless `.env` sets another `COMPOSE_PROJECT_NAME`. |
| **In plain words** | The host's shared scheduler makes the database backups, so there is no backup container in this project. |
| **Reasoning** | Owner decision; callisto-services already runs Ofelia. Matters here more than in a plain template because the database holds the only copy of the recordings after the permanent delete at Plaud (D-004). |
| **Rejected alternatives** | See T-010. |
| **Status** | **FINAL** |

### D-008: Web UI as its own service with loopback access, host and CSRF checks, tombstones for discards (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | The UI (`webapp.py`, FastAPI, Jinja2 templates, `recording_service.py` for the queries) runs as its own Compose service `web` from the same image as `app` (`image: <project>-app`, `pull_policy: never`, command `uvicorn webapp:app`). It gets database settings only, no Plaud credentials. The port is `127.0.0.1:${PORTS_PREFIX}010`; access control is the SSH tunnel, there is no login. Against a hostile web page in the owner's browser it checks the `Host` header (`localhost`, `127.0.0.1`, `[::1]` plus `UI_ALLOWED_HOSTS`), requires a CSRF token (HMAC of a per-process secret, or `UI_SECRET`) plus a matching `Origin` on every POST, escapes all stored text, shows the summary as plain text and sets a Content-Security-Policy that forbids scripts. API docs are off. Discarding is two steps: a GET confirmation page, then a POST. Discarding removes title, summary and segments, keeps a tombstone row (`plaud_id`, hashes, `discarded_at`) and the import skips it before any API call. The bulk discard ("older than N days") POSTs the count the owner saw; if the count changed, nothing is discarded. List paging is keyset-based. Discarded rows are excluded from the trash step; a discarded row that was already trashed still reaches the permanent delete, because its import was verified. Whether a discard should also remove a still-listed recording at Plaud is open (REQ-002 criterion 7). `redeploy.sh` stops, removes, starts and health-checks `web` together with `app`. Migration 001 adds `recordings.discarded_at`. |
| **In plain words** | You open a small page on your server through your tunnel, look at everything that was imported and throw away what you do not need. Nothing on that page can be triggered from another website. |
| **Reasoning** | Owner wants to see and clean up the stored recordings. The page shows private conversations and can delete, so it is built defensively even though it is only reachable on loopback: a website open in the same browser can otherwise send requests to a localhost port. A separate service keeps Plaud tokens out of the web process and lets the UI restart without interrupting the import cycle. The tombstone is needed because a plain delete would be re-imported on the next cycle while the recording is still at Plaud. |
| **Rejected alternatives** | (A) UI inside the `app` process: shares credentials and restarts with the cycle; (B) login with a password: the SSH tunnel already authenticates, a second secret adds nothing now; (C) hard delete of the row: re-import; (D) JavaScript confirm dialogs: blocked by the CSP and not needed with a confirmation page; (E) offset paging: skips and duplicates while the import adds rows. |
| **Status** | **FINAL** |

### D-009: A discarded recording is also deleted at Plaud, under age and duration gates only (FINAL)

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Decision** | Amends D-008 on the Plaud side. A recording discarded in the UI stays in the deletion pipeline. Its trash conditions are reduced to the minimum age (`MIN_AGE_MINUTES`) and a known duration; the processing, verification and stability conditions are skipped, because the content was removed on purpose. The permanent delete follows after `PERMANENT_DELETE_AFTER_HOURS` and no longer needs a verified import for a discarded recording. `PLAUD_DELETE_ENABLED=false` still only logs. The UI's confirmation pages say whether Plaud deletion is on (with the age and wait hours) or off. `web` therefore reads `MIN_AGE_MINUTES`, `PERMANENT_DELETE_AFTER_HOURS` and `PLAUD_DELETE_ENABLED`. |
| **In plain words** | When you throw a recording away in the page, it is also removed at Plaud on the normal schedule, so nothing of it is left anywhere. |
| **Reasoning** | Owner decision. Leaving it at Plaud would keep conversation content there for good, against the purpose of the tool (D-004). The verification gate protects the only copy; after a deliberate discard there is no copy to protect. |
| **Rejected alternatives** | (A) leave discarded recordings at Plaud: contradicts data minimisation, was the safe default only until the owner decided; (B) delete at Plaud immediately on discard: skips the age and duration protection against unfinished recordings and the Plaud trash step. |
| **Status** | **FINAL** |

### D-010: Defaults are 12 hours minimum age and 12 hours trash wait, about one day in total (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends the default values of D-006. `MIN_AGE_MINUTES` defaults to 720 (12 hours) and `PERMANENT_DELETE_AFTER_HOURS` to 12, so a recording is gone at Plaud about one day after it ended: in the Plaud trash after 12 hours, deleted permanently 12 hours later. Both stay configurable and an explicit value in `.env` wins over the default. The duration guard of D-006 and the discard rules of D-009 are unchanged. |
| **In plain words** | After about one day nothing of a recording is left at Plaud. Half a day passes before it goes into the Plaud trash, and half a day more before it is deleted for good. |
| **Reasoning** | Owner decision: the owner wants the content gone at Plaud after one day in total, and keeps the Plaud trash as a half-day safety net. D-006's one day plus 24 hours meant about two days. |
| **Rejected alternatives** | (A) 24 h plus 24 h (about two days at Plaud): longer than the owner wants; (B) 24 h plus 0 (no trash step): nothing restorable at Plaud. |
| **Status** | **FINAL** |

### D-011: MCP server as its own opt-in service with a static bearer token, hand-written minimal protocol (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Claude reaches the recordings through `mcp_server.py` (adapter) and `mcp_tools.py` (tools), run as the Compose service `mcp` from the same image, on its own port `127.0.0.1:${PORTS_PREFIX}020`, behind the owner's reverse proxy. The service is opt-in (`profiles: [mcp]`, enabled by `COMPOSE_PROFILES=mcp`); `redeploy.sh` manages and health-checks it only then. Protocol: MCP over Streamable HTTP, stateless, one endpoint `POST /mcp` with JSON responses, notifications answered 202, GET and DELETE 405, no sessions, no server-initiated stream; protocol versions 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05 are negotiated (the tools-only subset is identical). Authentication: every request needs `Authorization: Bearer <token>`; tokens come from `MCP_TOKENS` (one, or two during a rotation), at least 32 characters, compared in constant time; the service refuses to start without a valid token. Further checks: `Host` allowlist (`localhost`, `127.0.0.1`, `[::1]`, `MCP_ALLOWED_HOSTS`), `Origin` must be absent or in `MCP_ALLOWED_ORIGINS`, request body at most 64 KB. Two read-only tools: `list_recordings` (search, date filters, cursor) and `get_recording` (summary and transcript in slices). Tool descriptions state that recording text is untrusted data. Logging records tool, argument names and result size only, never text, search words or tokens; failed logins are logged with the client address. `GET /healthz` answers `ok` or 503 without detail for the container health check; the reverse proxy should forward only `/mcp`. No new dependency: the protocol subset is about 150 lines on top of FastAPI. |
| **In plain words** | Claude gets its own small door to your recordings, on its own port and behind your reverse proxy. The door opens only for the secret token, and Claude can only look, never change or delete. |
| **Reasoning** | Owner wants Claude to work on the recordings, kept on his own server (D-004). Claude Code accepts a bearer header, and claude.ai accepts a fixed header for organisations in a beta (documented by Anthropic); a static token is the simplest authentication that is safe if it is long and random and the proxy provides TLS. Only tools are needed, so a small hand-written server is easier to audit and test than a framework; it was checked against the official MCP Python client. A separate service keeps the internet-facing surface apart from the UI and from the Plaud credentials: it only needs database access. |
| **Rejected alternatives** | (A) official MCP SDK server: pulls in a large dependency tree and its own web stack for a tools-only server (installing the SDK in a test environment already moved the pinned Starlette version); (B) same port as the UI behind path routing: mixes a public endpoint with the loopback-only UI; (C) no authentication ("No sign-in" connector): anyone with the URL could read every conversation; (D) OAuth now: much more code and state for a single user, and only needed for claude.ai accounts without the request-header beta; to be built if the owner needs it; (E) write tools (discard, delete) in MCP: a prompt injection in a transcript could then delete data; discarding stays in the UI. |
| **Status** | **FINAL** |

### D-012: OAuth 2.1 for the MCP server with one pre-registered client, owner password and rotating tokens (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends D-011, which left OAuth out. The MCP service is its own authorization server for a single owner (`mcp_oauth_web.py` for the HTTP side, `mcp_oauth.py` for codes and tokens). Discovery: 401 with `resource_metadata`, protected-resource metadata (RFC 9728) at `/.well-known/oauth-protected-resource` and `…/mcp`, authorization-server metadata (RFC 8414) at `/.well-known/oauth-authorization-server`; issuer and resource come from `MCP_PUBLIC_URL`, never from the request. One pre-registered client (`MCP_OAUTH_CLIENT_ID`, optional `MCP_OAUTH_CLIENT_SECRET`); no dynamic client registration and no client ID metadata documents, so the server has no endpoint through which an anonymous caller can create state. PKCE with S256 is mandatory. Redirect addresses are matched exactly against `MCP_OAUTH_REDIRECT_URIS` (default Claude's fixed callback `https://claude.ai/api/mcp/auth_callback`); a listed loopback entry without a port matches any port. `/authorize` shows a login page; the owner proves identity with `MCP_OAUTH_PASSWORD` (at least 20 characters). The form carries the request in a signed, 10-minute blob (HMAC with a per-process key), so a hidden field cannot be changed. Failed logins are serialised and each takes twice as long as the one before (1 s up to 30 s), reset on success. Codes live 60 seconds and work once; access tokens 1 hour; refresh tokens 30 days and are replaced on every use. Tokens are random 256-bit values stored as SHA-256 hashes in `oauth_codes` and `oauth_tokens`. Codes and tokens form a family: reusing a code or a spent refresh token revokes the whole family. The `resource` parameter, if sent, must equal `<MCP_PUBLIC_URL>/mcp`. The `/mcp` endpoint accepts a static token from `MCP_TOKENS` or an OAuth access token. Any single OAuth setting switches OAuth on and demands a complete configuration, otherwise the service does not start. The service now writes to the two OAuth tables; recordings are still only read. `MCP_ALLOWED_HOSTS` takes host names; a pasted URL is reduced to its host. |
| **In plain words** | claude.ai connects to your server like to any other service: it sends you to a page on your own server, you type a password once, and after that Claude renews its own access every hour for up to 30 days. Nobody else can sign in, and tokens that were stolen and replayed are shut down. |
| **Reasoning** | claude.ai accepts a fixed header only for organisations in a beta, so OAuth is the dependable way for the web, desktop and mobile apps. One owner means one pre-registered client is enough, and "use your own OAuth client" is an option of Claude's connector dialog. Skipping dynamic registration removes the one endpoint that would be open to the internet. Hashed tokens mean a database copy is no login. Rotation with family revocation follows OAuth 2.1 for public clients, and Claude's documentation asks for it. |
| **Rejected alternatives** | (A) dynamic client registration: an open endpoint that anyone can fill with registrations; (B) client ID metadata documents: the server would fetch URLs supplied by callers (SSRF surface); (C) a hosted identity provider: another service and account for one user; (D) JWT access tokens without a database: no revocation and no way to detect a replayed refresh token; (E) a long-lived token typed into the connector: only possible for organisations with the header beta; (F) approval without a password: anyone who finds the URL could grant themselves access. |
| **Status** | **FINAL** |

### D-013: Tasks, topics and digests in the voice-vault database; Claude writes only there, with log and undo (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends D-011 and D-012, which said the MCP service only reads recordings. New tables `topics`, `todos`, `todo_events`, `topic_notes` and `digests`; all rules live in `todo_service.py`, used by the MCP tools (`mcp_todo_tools.py`) and the UI (`webapp.py`). Claude gets tools to list, add and update tasks (including status `done` and `dropped`), list topics, read a topic with its timeline, add a topic note, mark a recording analyzed, and save and read daily digests. The only write to `recordings` is the `analyzed_at` flag. There is no delete: neither a tool nor a UI route removes a task, topic, note, digest or recording, and a test enforces that no function in the service deletes. Every change writes a `todo_events` row with actor (`claude` or `owner`), kind, old and new values and the source recording. Undo applies the old values and is allowed only for the latest event of a task and only if the task is unchanged since. Similar open tasks (Jaccard 0.6 over words of three letters or more) are rejected for Claude so that a daily routine does not pile up duplicates. Write tools are annotated `readOnlyHint false, destructiveHint false`. Tool errors that the model can fix (duplicate, bad date, unknown id) are tool results with `isError`, type errors are -32602. The OAuth approval text tells the owner that Claude may maintain tasks. |
| **In plain words** | Claude may write the to-do list, nothing else. Everything it writes is marked as Claude's, listed in the history of the task and can be undone with one click. It can mark a task as no longer relevant, but never delete it. |
| **Reasoning** | The owner wants a routine that works without him and a list he only checks off. That needs write access. The risk is a prompt injection in a transcript; with no delete and an undo for each change, the worst case is a wrong or missing task. Recordings stay protected, which was the point of D-011. Keeping the list in the same database avoids a second system that holds the contents of the conversations. |
| **Rejected alternatives** | (A) read-only MCP plus Google Tasks or a Markdown file: second storage place for the content, and no undo; (B) free-text list in one document: no priorities, no check-off, no history; (C) Claude may delete tasks: an injected instruction could wipe the list; (D) no duplicate check: the daily run would re-create tasks from every follow-up conversation; (E) undo of any past event: later changes make old values wrong. |
| **Status** | **FINAL** |

### D-014: Defaults are 7 days minimum age and 3 days trash wait, about ten days in total (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends the default values of D-010. `MIN_AGE_MINUTES` defaults to 10080 (7 days) and `PERMANENT_DELETE_AFTER_HOURS` to 72 (3 days). A recording goes into the Plaud trash 7 days after it ended and is deleted permanently 3 days later. Both stay configurable, and an explicit value in `.env` wins over the default, so a `.env` that still sets the old values has to be changed. All other gates (processed, verified, stable, known duration) and the discard rules of D-009 are unchanged. |
| **In plain words** | Plaud keeps a recording for a week, then it sits in the Plaud trash for three more days before it is gone for good. |
| **Reasoning** | Owner decision. With the daily routine of REQ-006 and a first backup that only worked after a fix, a longer period gives time to notice a problem while the original is still at Plaud. Seven days also match the routine's look-back window. |
| **Rejected alternatives** | (A) keep 12 h plus 12 h (D-010): little room to notice a failed import, backup or routine; (B) 7 days with no trash step: nothing restorable at Plaud. |
| **Status** | **FINAL** |

### D-015: Only the owner's own tasks; topics can be excluded by the owner (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends D-013. The task list holds only what the owner himself has to do; commitments of other people are not tasks. This is enforced by the tool description and the routine prompt, not by code, because only the content of a conversation tells who has to act (the speaker labels from Plaud are unreliable). The owner can exclude topics in the UI (`topics.excluded`, set by name, also before the topic exists). For an excluded topic `add_todo`, `update_todo` (setting that topic) and `add_topic_note` are refused with a tool error, its open tasks are left out of `list_todos` and of the counts (the topic page still shows them), and `list_topics` marks it `EXCLUDED by owner`. Claude has no tool to set or clear the flag. Migration 003 adds the column. Claude still reads the recordings of an excluded topic; excluding stops storing derived data, not reading. |
| **In plain words** | The checklist is yours only. What others promised you is not on it. Topics you do not want tracked get no tasks and no notes from Claude. |
| **Reasoning** | Owner decision: the list to tick off must stay short and his own; follow-up on others is a different kind of tracking. A flag that only the owner can set keeps a prompt injection from lifting an exclusion. |
| **Rejected alternatives** | (A) an assignee field on each task and a second list for others: the owner wants tracking of others elsewhere; (B) hiding excluded topics only in the UI: Claude would still file tasks and notes into them; (C) stopping Claude from reading such recordings: the topic is only known after reading, and a transcript can touch several topics; (D) a Claude tool to exclude topics: weakens the guarantee. |
| **Status** | **FINAL** |

### D-016: Daily overviews are dated by the day of the conversations (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends D-013. A digest belongs to the day the recordings were made, taken from the Plaud start time in local time with the 04:00 day boundary (`utils.local_day`, `utils.day_bounds`), not to the day the routine runs. `save_digest` is refused for a day on which no stored, not discarded recording started, and the error lists the recent days that have recordings. A run over several days writes one digest per day and extends an existing one instead of replacing it blindly. The UI lists the recordings of the shown day. |
| **In plain words** | The overview of Tuesday's conversations is filed under Tuesday, even though the routine runs on Wednesday morning. |
| **Reasoning** | Owner decision: the routine runs the morning after, so the run date would always be one day too late. The server checks the date because a prompt cannot be relied on to get it right every day. |
| **Rejected alternatives** | (A) trust the day Claude passes: a wrong date goes unnoticed and files the overview in the wrong place; (B) the server picks the day from the recordings and ignores the argument: a run over a weekend could not be split into days; (C) UTC midnight as the day boundary: late-evening recordings would land on the next day. |
| **Status** | **FINAL** |

### D-017: The owner can delete topics, in a separate module and behind a confirmation page (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends D-013, which allowed no deletion at all. Only the owner can delete, and only topics. `topic_admin.py` holds the deletion; `todo_service.py` keeps having no delete function, and a test checks that the MCP tools and the service never reference `topic_admin`. The UI asks first (`/topics/{name}/delete`) and shows how many notes go and how many tasks stay. Deleting removes the topic and its notes for good. Its tasks stay, lose the topic, and each of them gets a logged `updated` event with the old topic, so the owner can undo it per task. A checkbox, on by default, keeps the topic as an excluded one (D-015) so the next routine run does not create it again from a conversation. The task list can be sorted by priority (default, grouped), due date, newest, oldest, topic or title. |
| **In plain words** | You can delete a topic after a confirmation. Its notes are gone, its tasks stay on the list, and by default Claude is told not to bring the topic back. |
| **Reasoning** | Owner decision: the topic list grows with every run and holds names he does not want. Tasks are his work and are not thrown away with a label. The exclusion default is there because the routine would otherwise recreate the topic the next morning. Keeping the code in its own module keeps the guarantee that nothing Claude can reach deletes data. |
| **Rejected alternatives** | (A) delete the tasks of the topic as well: one click could wipe the list; (B) a delete tool for Claude: a prompt injection could remove topics; (C) only hiding topics (exclusion): the list of topics would keep growing; (D) delete without a confirmation page: topic notes cannot be restored. |
| **Status** | **FINAL** |


### D-018: Topics are addressed by id; the owner can rename them (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends D-017. The topic pages use the numeric id (`/topics/{id}`, `/topics/{id}/delete`, `POST /topics/delete` with `topic_id`), not the name, because a name such as "Wiz / DORA-Vertragsrisiko" breaks a path (the slash) and may hold other characters; a name is a label, not an address. The owner renames a topic on its page (`POST /topics/{id}/rename`). A rename keeps tasks, notes and the exclusion flag, since they reference the id. A name another topic already has is refused (case-insensitive); there is no merge. Old task events keep the old name in their JSON, so an undo of an event from before a rename is refused as "changed since" instead of creating the old topic again. The MCP tools still find topics by name, since Claude sees names only, and have no rename tool. Errors in the web UI now show a readable page with the same status code. |
| **In plain words** | A topic can be called anything and renamed later without anything breaking. |
| **Reasoning** | Reported by the owner: the delete page for a topic with a slash returned "Not Found". Ids are stable under renaming and need no escaping. |
| **Rejected alternatives** | (A) escape the name in the path: the server decodes `%2F` before routing, so the slash would still break the route; (B) a name in a query parameter: works, but links would break on every rename; (C) automatic merge on a name clash: silently moves tasks between topics. |
| **Status** | **FINAL** |

### D-019: Deleting at Plaud follows what the import of the same cycle saw (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Result of the security review. Amends D-003 and D-006. (1) The deletion step of a cycle gets the result of the import of the same cycle: a recording is trashed only if Plaud listed it just now, its transcript and summary were read in full and the read-back verified it (`ImportStats.seen`, `.confirmed`). A discarded recording only has to be listed. An empty list trashes nothing. A recording that is no longer listed is remembered as trashed without calling Plaud, so the permanent delete can follow. (2) A recording that shows up outside the Plaud trash again loses its `trashed_at`, so a restore by the owner cancels the permanent delete. (3) Content counts only with real text: a summary and at least one utterance that is not blank. Text is cleaned of NUL characters and lone surrogates. A start time below 2015 or more than a day ahead fails the import of that recording. A transcript that ends in the first third of a recording of two minutes or more is stored, but never verified, so never deleted. (4) A download answers only with HTTP 200; redirects, error pages in XML or HTML, truncated or oversized gzip (limit 50 MB) and invalid UTF-8 are errors, not content. Download links must be https to a named host, not an address. Plaud ids must match `[A-Za-z0-9_-]{1,64}` before they reach a path or a request. (5) One unexpected error in a recording no longer stops the cycle; the recording counts as failed. Three failing cycles in a row send one mail a day. (6) Gate settings below 1 stop the start; at most `MAX_TRASH_PER_CYCLE` (20) recordings are trashed per cycle. (7) A migration waits at most 10 seconds for a lock; the Plaud token pair is saved with retries. |
| **In plain words** | Nothing is thrown away at Plaud unless the copy was read in full and checked a moment ago. If Plaud's answers look wrong, nothing is deleted. |
| **Reasoning** | The review showed several ways in which a change in Plaud's unofficial API (a renamed key, a redirect, a list that comes back empty, a missing start time) could produce a verified-looking but empty copy, or make every recording look old, and then delete the original. The database alone could not tell. Losing a recording is the worst outcome, so every doubt has to end in "do not delete". |
| **Rejected alternatives** | (A) trust the database state as before: it is stale by design when the API drifts; (B) a start-time gate from `created_at`: delays deletion of a backlog and breaks the fixed clocks in the tests; (C) allow-list of download hosts: Plaud's storage hosts are not documented, a wrong list would stop every import; (D) treat a short transcript as an error: a recording with a long silence would never be imported. |
| **Status** | **FINAL** |

### D-020: Findings of the security review and what was left open (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Four independent reviews (MCP and OAuth, web UI and task logic, Plaud import and deletion, deployment and git history) found no authentication bypass, no OAuth flaw, no XSS and no secret in the git history. What they found was fixed: **Plaud/deletion** see D-019. **MCP and OAuth**: the lock after wrong approval passwords no longer sleeps while holding a global lock (a way to keep the owner out and to tie up workers); a locked client is refused at once and its password is not checked, per client address when `MCP_TRUST_FORWARDED_FOR=true`, otherwise for all; bodies of `/token` and `/authorize` are limited to 8 KB and must be urlencoded; deeply nested JSON, NaN and ids that are not strings or integers no longer cause a 500; a batch has at most 20 messages; log text is stripped of control characters. **Task logic**: tasks of an excluded topic no longer leak through the duplicate check, cannot be changed by Claude (same answer as for an unknown id), and the topic is not named in `list_topics`; an undo cannot put a task back into an excluded topic; overwritten digests and topic notes are kept as versions; at most 2000 open tasks; the owner can reset the analyzed mark. **Web UI**: ids are bounded, odd cursors give a clean error, unhandled errors still carry the security headers, the origin must equal `scheme://host` and `Sec-Fetch-Site: cross-site` is refused, the cleanup and the topic deletion confirm the exact set the owner saw (fingerprint, expected counts), not only a count. **Operations**: backups are written with mode 600 into a folder of mode 700, verified with `gzip -t` and a minimum size, and `BACKUP_RETENTION_DAYS` must be a whole number of at least 1; `redeploy.sh` checks the compose file, the backup folder and warns about a weak database password before it stops anything; a reference nginx configuration with exact paths, rate limits, body limits and a catch-all 404 is in the README; `app`, `web` and `mcp` drop all capabilities and set `no-new-privileges`; more secret-like file types are ignored by git; fastapi 0.142.2 (Starlette 1.7) and jinja2 3.1.6 replace the pinned versions with known advisories, verified with the full tests and with the official MCP client against a running server. **Left open on purpose**: (1) one database role per service (the `mcp` service still holds the database owner's credentials, so a code-execution bug in it would also reach the Plaud token pair); (2) read-only container file systems (not testable without Docker here); (3) the `shared_net` network on the database (probably for Adminer; the owner decides); (4) the `app-tests` service in the default profile; (5) image tags by digest and a multi-stage Dockerfile with a `USER`; (6) `deploy.yml` posts the tail of a failing deploy log as a PR comment (workflows are not changed without approval); (7) Plaud stays an unofficial API: any drift is caught by D-019 only as far as it shows in the data. |
| **In plain words** | Nobody could get in through the front door. The weak spots were: a way to lock the owner out of the approval page, a few places where Claude or an odd request could do more than intended, and several ways in which a change on Plaud's side could have led to a recording being deleted without a good copy. These are closed. A few bigger hardening steps need a decision from the owner and are listed in the README. |
| **Reasoning** | The code was written and tested by the same author; independent reviewers with the task to break it are the best check available without an external audit. Every finding was reproduced by a probe before it counted. |
| **Rejected alternatives** | (A) a CAPTCHA or e-mail confirmation for the approval page: more moving parts for one user; (B) per-address locks without a trusted proxy header: the address cannot be known reliably, a spoofed header would then unlock the guess limit; (C) leave the dependency versions: the advisories concern multipart and file responses, which the public endpoints do not use, but the upgrade passed all checks and removes the question. |
| **Status** | **FINAL** |

### D-021: Referrer policy `same-origin` for the web UI, and `Origin: null` only from the browser's own same-origin requests (FINAL)

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Decision** | Amends D-008 and D-020. The web UI sends `Referrer-Policy: same-origin` instead of `no-referrer`. With `no-referrer` browsers send `Origin: null` on every form post, even to the same site, and the origin check of D-008 then refused every button of the UI ("Nicht erlaubt"). The CSRF check still needs the token and an origin that equals `scheme://host`; `Origin: null` is accepted only together with `Sec-Fetch-Site: same-origin`, which a web page cannot set. `Sec-Fetch-Site: same-site` (another port on the same host) and `cross-site` are refused. |
| **In plain words** | The buttons work again. The protection against forged requests from other websites is the same as before. |
| **Reasoning** | Reported by the owner. The tests used a client that sets the headers by hand and so never saw the browser's behaviour; it was reproduced in Chromium (old policy: `Origin: null`, refused; new policy: real origin, accepted). |
| **Rejected alternatives** | (A) drop the origin check: the CSRF token alone protects, but the origin check is a second layer that costs nothing; (B) accept `Origin: null` always: a sandboxed page of another site sends `null` too; (C) keep `no-referrer` and rely on the token only: same as A. |
| **Status** | **FINAL** |

### D-022: Deleting at Plaud right after the import, trash included (FINAL)

| | |
|---|---|
| **Date** | 2026-10-13 |
| **Decision** | Amends D-014. The defaults become `MIN_AGE_MINUTES=1` and `PERMANENT_DELETE_AFTER_HOURS=0` (0 is allowed now; `STABILITY_MINUTES` stays 10). A recording that arrived here, was read in full and verified goes into the Plaud trash and is deleted from it in the same cycle once its content has been unchanged for the stability time, so about one cycle (10 minutes) after the import. The other gates stay: Plaud reports both tasks done, duration known, verified read-back, listed and verified in this very cycle (D-019), at most `MAX_TRASH_PER_CYCLE` per cycle. `PLAUD_DELETE_ENABLED` stays the master switch and is `false` by default; a value in `.env` overrides every default above. The confirmation page of a discard says "right away" when both values are at these defaults. |
| **In plain words** | As soon as a conversation is safely here, it is gone at Plaud, from the trash as well. |
| **Reasoning** | Owner decision: the point of the tool is to keep as little as possible at Plaud. The 10 minutes of stability stay on purpose: Plaud sometimes still changes a summary or the speaker names right after processing, and a change inside that window restarts the clock instead of deleting a stale copy. |
| **Rejected alternatives** | (A) delete in the same cycle as the import (stability 0): no second look at Plaud's content before deleting; (B) keep the trash step for a day as a safety net (D-006, D-010, D-014): the owner does not want it; (C) delete without the trash step: the unofficial API needs the trash first. **Consequence the owner accepted:** nothing can be restored at Plaud any more; between two backups (every 4 hours by default) the live database is the only copy. |
| **Status** | **FINAL** |

### D-023: A recording is imported only when most speakers have names (FINAL)

| | |
|---|---|
| **Date** | 2026-10-13 |
| **Decision** | New gate in the import. After the transcript has been read, the different speaker labels are counted. A label is unnamed if it is empty or looks like Plaud's own ("Speaker 2", "Sprecher 1", "Redner", "Unbekannt", with or without a number). If fewer than `MIN_SPEAKERS_NAMED_PERCENT` (default 80, range 0 to 100, 0 switches it off) of the labels are named, the recording is skipped: nothing is stored, nothing is deleted, the log says `Waiting for speaker names` with the counts. The check is repeated every cycle, so naming the speakers in Plaud is enough; the next cycle imports. It applies to stored recordings too: one whose names are gone again is not confirmed for deletion. Titles and texts are not logged. |
| **In plain words** | A new conversation is checked for speaker names first. Too few names: it stays at Plaud and is looked at again in ten minutes. Enough names: it is downloaded and then deleted at Plaud. |
| **Reasoning** | Owner decision. The speakers are named in Plaud, and after the deletion that is no longer possible, so the names have to be there first. |
| **Rejected alternatives** | (A) import first, name later here: the names would not be in the stored copy, and the Plaud original is gone; (B) count the share of utterances instead of speakers: a name for the one who speaks most would be enough and hide unnamed others; (C) skip for good after one try: the owner names speakers after the recording. **Open point:** a recording whose speakers are never named stays at Plaud and is not visible here; the log is the only trace. |
| **Status** | **FINAL** |

### D-024: Half of the speakers named, and at most seven days of waiting for names (FINAL)

| | |
|---|---|
| **Date** | 2026-10-19 |
| **Decision** | Amends D-023. `MIN_SPEAKERS_NAMED_PERCENT` defaults to 50. A recording with fewer named speakers waits at most `SPEAKER_NAMING_WAIT_DAYS` (default 7, 0 means no waiting), counted from the end of the recording. After that it is imported with the names it has, logged as `Speaker naming time is over, importing anyway`, verified, and deleted at Plaud like every other recording (D-022). It is never deleted at Plaud without being stored here first. |
| **In plain words** | Half of the speakers need names. If they do not get them within a week, the conversation is downloaded anyway and then deleted at Plaud. |
| **Reasoning** | Owner decision: the speakers are usually named, 80 percent held back too much, and a recording must not stay at Plaud forever only because nobody named the speakers. |
| **Rejected alternatives** | (A) delete at Plaud after seven days without downloading: the conversation would be lost; (B) no time limit (D-023): unnamed recordings stay at Plaud for good and are invisible here. |
| **Status** | **FINAL** |

### D-025: The transcript is read from Plaud's current version, not from the download link (FINAL)

| | |
|---|---|
| **Date** | 2026-10-19 |
| **Decision** | `PlaudClient.fetch_segments` first asks `POST /file/list` with the recording id and takes `trans_result` of that file: the transcript as it is now, with the speaker names the owner gave in Plaud and his text corrections. Only if that answer has no transcript (or the call fails, which is logged) does it fall back to the file behind the transcript's `data_link`. That file is the state right after transcription and knows only the voices Plaud recognised itself. Stored recordings whose names changed are re-imported and verified again through the existing content hash. |
| **In plain words** | voice-vault now sees the speaker names you set in Plaud. Before, it saw only the first automatic version of the transcript. |
| **Reasoning** | Reported by the owner: a recording with all speakers named in Plaud was held back as "1 of 3 named". The reference client `plaud-api` reads and writes speaker names through `/file/list` and `PATCH /file/{id}`, `plaud-tools` writes renames into `trans_result` as well. The download link is not updated by those edits. Not yet confirmed against the live API; the fallback keeps the import working if the call is refused. |
| **Rejected alternatives** | (A) count names from the summary's participant line: AI text, it repeats or invents names; (B) map `original_speaker` through Plaud's global speaker list (`/speaker/list`): voice profiles, not the names given in one recording. |
| **Status** | **FINAL** |

### D-026: The cleaned transcript (with the speaker names) is the one that is read and stored (FINAL)

| | |
|---|---|
| **Date** | 2026-10-19 |
| **Decision** | Amends D-025. Plaud keeps two transcripts: the original (`transaction`, "Transkript Original") with Plaud's labels such as "Speaker 1", and the cleaned one (`transaction_polish`, "Transkript bereinigt") that carries the speaker names the owner gives and smooths the wording. `fetch_segments` reads, in this order: the cleaned transcript from `POST /file/list`, the cleaned transcript's download link if that block is finished, the current original from `/file/list`, the original's download link. A fallback to the original is logged. The cleaned transcript is what is stored, so the stored copy and Claude see the names; the speaker check of D-023/D-024 counts on it. Recordings stored earlier get the cleaned version through the next re-import (content hash changes). |
| **In plain words** | voice-vault now reads the cleaned transcript, where your speaker names are, and keeps that one. |
| **Reasoning** | Reported by the owner, confirmed with `plaud_live_check --speakers`: the original transcript in both API sources held "Speaker 1" (125 utterances) and "Speaker 3" (11) although the names were set in Plaud; the owner pointed to the cleaned transcript as the place of the names. |
| **Rejected alternatives** | (A) store the original and take only the names from the cleaned one: the two have different utterance boundaries, matching them is guesswork; (B) keep counting on the original: the names never show up there. |
| **Status** | **FINAL** |

### D-027: Checking off a task without reloading the page, with a script from this server only (FINAL)

| | |
|---|---|
| **Date** | 2026-10-20 |
| **Decision** | Amends D-008 and D-021, which allowed no JavaScript. The task list loads one script, `/static/todos.js`, served from the app (`StaticFiles`, `COPY static` in the Dockerfile). The Content-Security-Policy allows `script-src 'self'` and `connect-src 'self'`, still no inline code and no other origin. The script intercepts the check and "not relevant" buttons, posts the same form with `Accept: application/json` (CSRF token and origin check unchanged), fades the row out, updates the tab and group counts from the answer and shows a notice with "Undo" for six seconds; undo posts the previous status back. If anything fails, the form is submitted the normal way, so errors show as before. Without JavaScript the page works as before. `[hidden]` always hides, also where CSS sets `display`. |
| **In plain words** | Ticking off a task now only removes that row; the page stays where it is, and an "Undo" appears for a moment. |
| **Reasoning** | Owner request. A same-origin script file keeps the protection of the policy: an injected text still cannot run code, because inline scripts stay forbidden. Checked in Chromium: no navigation, counts right, empty groups disappear, undo restores, no console errors. |
| **Rejected alternatives** | (A) inline script with a nonce: more moving parts per response for the same effect; (B) a front-end framework: a dependency and a build step for one interaction; (C) no undo: an instant, silent removal makes a slip easy and hard to notice. |
| **Status** | **FINAL** |

### D-028: Status "in progress" and a reminder of those tasks on every page (FINAL)

| | |
|---|---|
| **Date** | 2026-10-20 |
| **Decision** | Tasks get a fourth status `in_progress` (column widened to 20 characters, migration `004_todos_status_length`). It counts as unfinished: the filter `open` (UI tab, `list_todos`, counts, duplicate check, the 2000 limit, topic counters) covers `open` and `in_progress`; `in_progress` has its own tab and filter. Within a priority, tasks in progress come first. Starting is logged as event `started` and can be undone. In the list, "Start" and "Put back" change the row in place (the row stays, a marker shows the status). Every GET page loads the tasks in progress (at most 20, without excluded topics) through an app-wide dependency; if there are any, the header shows "In progress (n)", which opens a native HTML popover with "Done", "Put back" and "Note" (link to the task's detail text). A second script `/static/wip.js` opens the popover once per day by itself (date in `localStorage`) and updates it without reloading. Without JavaScript the buttons still work; the form returns to the page it came from, but only to a Referer of this host. |
| **In plain words** | Tasks can be "In Arbeit". A button at the top of every page shows how many and lists them with "Erledigt" and "Zurückstellen"; once a day the list pops up by itself and asks how things are going. |
| **Reasoning** | Owner request: a status in progress plus something to see those tasks as a reminder and for quick access. Counting them as open keeps the routine, MCP and the existing "Open" view unchanged in meaning: a started task is still to do. The popover API needs no inline script and no library. |
| **Rejected alternatives** | (A) a modal that blocks every page until answered: annoying several times a day; (B) a separate page only: no reminder; (C) `in_progress` outside "open": tasks would vanish from the main list and from Claude's default view once started. |
| **Status** | **FINAL** |

### D-029: The in-progress reminder opens only on click, follows every change live, and "in progress" is amber (FINAL)

| | |
|---|---|
| **Date** | 2026-10-20 |
| **Decision** | Amends D-028. The popover no longer opens by itself (no `localStorage`). The header count and the popover are always in the page (hidden at zero). Every JSON answer of `/todos/{id}/status` carries the task id and the current tasks in progress (`in_progress`, at most 20, id and title); `wip.js` rebuilds the panel from it with text-only DOM nodes and sets the count from `counts.in_progress`. The two scripts talk through one `todo:changed` event: a change in the list updates the header, a change in the panel updates the row and the tab counts in the list. Colours: open stays neutral, in progress is amber (`--progress`: chip, left bar, light row tint, header count), done stays green, red stays reserved for urgent and overdue. |
| **In plain words** | The list at the top only opens when you click it. Starting, finishing or putting back a task changes the number at the top straight away. "In Arbeit" is now amber, so it stands out. |
| **Reasoning** | Owner feedback: the automatic popup was irritating, the count needed a reload, and the blue marker was hard to see (blue is also the "normal" priority). Amber for "in progress" follows the common traffic-light pattern of task tools (grey open, amber/yellow in progress, green done); red would collide with "urgent" and "overdue". |
| **Rejected alternatives** | (A) reload the page after a change: loses the scroll position, the reason for D-027; (B) a second request for the panel after each change: one more round trip for data the answer can carry; (C) red for in progress: already means urgent/overdue here. |
| **Status** | **FINAL** |

### D-030: Tasks name the people to talk to, stored on the task, suggested from the transcript speakers (FINAL)

| | |
|---|---|
| **Date** | 2026-10-20 |
| **Decision** | A task gets a list of people (at most 10 names of at most 80 characters; duplicates in any case removed), stored in a new column `todos.people` as one name per line (migration `005_todos_people`). Changing the list is an ordinary task change: logged as `updated`, undoable, the same for adding and for taking a name off. The task page shows the people already named plus the speaker names of the task's recording as checkboxes; Plaud labels that match `UNNAMED_SPEAKER` are left out. A text field (comma-separated) takes more names and suggests every known name: names on tasks and speaker names of recordings not discarded. In the list, each name is a chip that filters by that person (`?person=`, whole name, any case, LIKE wildcards escaped). MCP: `add_todo` and `update_todo` take `people` (update replaces the whole list, `[]` clears it), `list_todos` takes `person` and prints the names. |
| **In plain words** | You can note on a task with whom you have to talk. The names from the recording are offered for ticking; untick a name to take it off. A click on a name shows all tasks with that person. |
| **Reasoning** | Owner request, names ideally from the transcripts, and taking a name off again must be possible. A column on the task keeps the people inside the task's snapshot, so history and undo work without extra code, and the service keeps its rule of never deleting rows. |
| **Rejected alternatives** | (A) a `people` table with a link table: needs deleting link rows to take a name off, which the service rule forbids, and its own history; (B) free text in the detail field: no filter, no suggestions; (C) a people page with merge and rename: not asked for; a misspelt name is fixed by ticking the right one. |
| **Status** | **FINAL** |

### D-031: People are picked as tags that save on every tap (FINAL)

| | |
|---|---|
| **Date** | 2026-10-20 |
| **Decision** | Amends D-030. On the task page the people are tags instead of checkboxes: an outlined "+ name" tag is a suggestion, a filled "✓ name" tag is assigned; a tap toggles it and `/static/people.js` saves the whole list at once through `POST /todos/{id}/people` (CSRF and origin check as everywhere, JSON answer with the stored names). A name becomes an assigned tag and is saved as soon as it is picked from the suggestion list (the `input` event without a typed character), or typed and confirmed with Enter or comma, or typed and the field is left. If a save fails, the tag flips back. The tags are still checkboxes underneath, so without JavaScript they are saved with the form's "Save" button. |
| **In plain words** | Names are tags now: tap one and it is on the task, tap it again and it is gone. No "Save" needed for that. |
| **Reasoning** | Owner request: checkboxes look dated; tags that toggle on a tap are what current tools do. |
| **Rejected alternatives** | (A) tags that change only on "Save": a tap that does not stick surprises; (B) a separate remove "×" on each tag: two targets per name where one toggle does. |
| **Status** | **FINAL** |

### D-032: The task page saves each field in the background when it is left; no Save button (FINAL)

| | |
|---|---|
| **Date** | 2026-10-20 |
| **Decision** | On the task page, `/static/task.js` listens for `change` (fired when a text field or the description is left, at once for status, priority and date) and posts the whole form to `POST /todos/{id}` with `Accept: application/json`; the route answers JSON (status, counts, tasks in progress) instead of redirecting. The people field's text input is left out of that post (people.js owns it). Saves run one after another in the order the fields were left. The field gets a short green border and the header shows "Gespeichert"; a rejected value (browser validation or HTTP error) shows "Nicht gespeichert, bitte prüfen" with a red border and stores nothing. Enter in a one-line field leaves the field instead of posting the page. A status change updates the status badge and, via the `todo:changed` event, the in-progress reminder. The Save button stays inside `<noscript>`, so without JavaScript the page works as before. The history list updates on the next load. |
| **In plain words** | On a task page you just change a field and click elsewhere: it is saved, the page stays put. |
| **Reasoning** | Owner request. Posting the whole form reuses the existing route and its checks; one queue keeps two quick edits from overtaking each other. |
| **Rejected alternatives** | (A) save on every keystroke: many history entries for one edit; (B) a separate route per field: more code for the same checks; (C) keep the Save button next to autosave: two ways to do one thing. |
| **Status** | **FINAL** |

### D-033: Markdown is formatted in recording summaries and task details, with a wider safe subset (FINAL)

| | |
|---|---|
| **Date** | 2026-10-20 |
| **Decision** | Amends REQ-002 criterion 3 (summary as plain text) and D-013's renderer. `markdown_lite` now also handles numbered lists (`<ol>`), lists nested by indentation, task boxes (`- [ ]`, `- [x]`), quotes (`>`, rendered recursively), rules (`---`, also the trailing `---` Plaud puts after a line), simple pipe tables and `__bold__`. It still escapes everything first and creates no links, images or raw HTML. The recording page shows the summary through it. On the task page the details show formatted; a click (or Enter) opens the text field, leaving it saves (D-032) and shows the server-rendered result again. Without JavaScript the text field stays as it was. |
| **In plain words** | Summaries and task details now look formatted: headings, lists, check boxes, quotes. To change the details, click on them. |
| **Reasoning** | Owner report: Markdown in the summaries was shown raw. A real Plaud summary uses quotes, nested lists, task boxes and rules, which the old subset did not cover. Escaping first keeps it safe; the CSP still forbids inline scripts. |
| **Rejected alternatives** | (A) a Markdown library (e.g. markdown-it): a dependency plus a sanitizer for a subset we fully control; (B) a client-side renderer: needs innerHTML from untrusted text in the browser; (C) a split editor with live preview: more than asked. |
| **Status** | **FINAL** |

### D-034: An admin area under /admin; first tool "Personen" deletes, renames and assigns names (FINAL)

| | |
|---|---|
| **Date** | 2026-10-21 |
| **Decision** | The header shows "Verwaltung" instead of "Aufräumen". `/admin` renders one tile per entry of `ADMIN_TOOLS` in `webapp.py`; a new admin tool is a new entry plus its routes under `/admin/...`, template and strings. The cleanup page keeps its URL and is listed there. First tool `/admin/people`, backed by the new module `people_admin` (web UI only, like `topic_admin`; MCP cannot reach it): (1) list of every name on a task or as a speaker of a kept recording, with tasks, recordings and passages; (2) rename: on tasks through `todo_service.update_todo` (logged, undoable), in transcripts by changing the segment label; onto an existing name this merges; (3) delete after a confirmation page whose counts are checked again (409 if changed): the name goes from every task (logged, undoable) and its passages get "Unbekannt N", the next free number per recording, so two deleted people of one recording stay apart; (4) "Ohne Namen": the unnamed labels (`UNNAMED_SPEAKER`) of the 50 newest kept recordings with two text samples each, and a field to give one label of one recording a name. Placeholder names such as "Speaker 7" are refused. Discarded recordings are not touched. Plaud is not changed: if Plaud still lists a recording and its content changes there, the next import replaces the stored transcript and with it these labels. |
| **In plain words** | "Verwaltung" in the header is the place for admin pages. On "Personen" you delete or rename names, merge two spellings, and give the unnamed speakers of a transcript a name. |
| **Reasoning** | Owner request, with the note that more admin tasks will come. One registry keeps the tiles and the navigation stable when tools are added. Task changes reuse the logged path, so a slip on a task can be undone; transcript labels have no history, hence the confirmation for deleting. |
| **Rejected alternatives** | (A) a people table with ids: the names live on tasks (D-030) and in segment labels; a table would have to be kept in sync with both; (B) clearing deleted speakers to an empty label: two people of one recording would merge into one unknown; (C) writing the names back to Plaud: no write API is used by this project and the recordings are deleted there after import; (D) one nav link per admin tool: the header fills up. |
| **Status** | **FINAL** |

### D-035: Statistics page with Chart.js served from this server (FINAL)

| | |
|---|---|
| **Date** | 2026-10-21 |
| **Decision** | New nav item "Statistik" (`/stats`), figures from the new read-only module `stats_service`: four tiles and four bar charts over the last 12 ISO weeks (local time, 04:00 day boundary): (1) new tasks versus tasks finished (done or dropped) per week, a finished task counted in the week of its last status change; (2) open tasks (open or in progress) at each week's end, stacked by priority, replayed from the status changes in `todo_events` (undo included) with today's priority; (3) open tasks per person, top 12; (4) conversation hours per week from `recordings.duration_ms`, discarded recordings included. Tasks of excluded topics are left out. Charts use Chart.js 4.4.7 (MIT), vendored as `static/vendor/chart-4.4.7.umd.min.js`; no CDN at runtime, the CSP stays `script-src 'self'`. The data goes into the page as a `<script type="application/json">` block (Jinja `tojson` escapes `<`, `>`, `&`), drawn by `/static/stats.js`. Every chart has a table view in a `<details>`. Colours: series blue/orange from the reference categorical palette, priorities in the colours the task list uses. |
| **In plain words** | "Statistik" shows whether the task pile grows, how urgent the open tasks are, with whom you have most open, and how many hours you spent in conversations, week by week. |
| **Reasoning** | Owner request; the owner preferred a chart library over hand-drawn SVG for hover tooltips and legends. Vendoring keeps the strict CSP and works offline. |
| **Rejected alternatives** | (A) server-drawn SVG: no dependency, but plainer interaction; (B) loading Chart.js from a CDN: needs a CSP exception and a third party on every page view; (C) storing weekly snapshots: the event log already allows replaying the past. |
| **Status** | **FINAL** |

### D-036: The topic of a task is picked as a tag, one per task (FINAL)

| | |
|---|---|
| **Date** | 2026-10-21 |
| **Decision** | The task page shows the topic like the people (D-031): tags for the current topic and the busiest non-excluded topics (8 in all), a text field suggesting every non-excluded topic. Under the tags are radio buttons; `/static/tags.js` (replaces `people.js` and serves both fields) lets a tap on the set topic clear it, a tap on another replace it, and saves through `POST /todos/{id}/topic` (JSON answer). Typed or picked topics become the tag at once. An excluded topic is refused (400) and the tag flips back. Without JavaScript the radio buttons and the text field (which wins if filled) are saved with the form. The data model stays: one topic per task. Tags are compact (0.8rem, filled light background, wrapping inside the tag instead of overflowing the card, softly rounded), the selected tag uses the accent on its soft background so it reads in dark mode too; 6 topic tags are offered; the side column of the task page is 21rem wide. |
| **In plain words** | Topics work like the name tags now: tap to set, tap again to remove, type a new one. A task still has one topic. |
| **Reasoning** | Owner request for the same mechanics as the people tags. One topic per task is kept because the topic page, the timeline, exclusion, the statistics and the MCP tools build on it. |
| **Rejected alternatives** | (A) several topics per task: a data model change across topic pages, timelines, exclusion and MCP, not asked for explicitly; (B) all topics as tags: the list grows without bound. |
| **Status** | **FINAL** |

### D-037: Task rows: title / status, topic, age / people; no author marker (FINAL)

| | |
|---|---|
| **Date** | 2026-10-21 |
| **Decision** | Each row of the task list has three lines: the title; then a status chip (open neutral, in progress amber, done green, dropped neutral), the topic and the age, followed by the due date and, in lists not grouped by priority, the priority; then the people tags, only if there are any. The "Claude" marker for tasks the routine created is gone from the list (the task page still says who created it). All four status chips are rendered and CSS shows the one matching `data-status`, so starting or putting back a task in place changes the chip without a reload. |
| **In plain words** | Each task in the list shows name, then status, topic and age, then the people. "Claude" no longer appears there. |
| **Reasoning** | Owner request. The due date and priority stay on the second line because "overdue" is the one warning in the list. |
| **Rejected alternatives** | (A) dropping due date and priority from the row: an overdue task would no longer stand out; (B) the status chip only for tasks in progress: the owner asked for the status in every row. |
| **Status** | **FINAL** |

### D-038: "Wartet auf" for what others promised, and a briefing page per person (FINAL)

| | |
|---|---|
| **Date** | 2026-10-21 |
| **Decision** | Reverses the rule of D-013/REQ-006 that commitments of other people are not recorded (owner decision). Tasks get a `kind` (`mine` default, `waiting`; migration `006_todos_kind`). A `waiting` entry is something another person (in `people`) promised the owner; it uses the same statuses, history, undo, topic and tags. Everything that measures the owner's own work filters `kind = mine`: the default list, the counts, the duplicate check (per kind), the in-progress reminder, topic counters and the statistics. The task list has two views, "Meine Aufgaben" and "Wartet auf" (no "in progress" tab there, no start or put-back buttons); the quick-add form of the waiting view asks "Von wem". MCP: `add_todo` and `list_todos` take `kind`; `list_todos` marks waiting entries as `waiting/<status>`. Briefing `/person?name=…` (module `briefing_service`, read only): the owner's open tasks with the person, what the person owes him, shared topics (from those tasks and from topic notes of the person's last ten conversations, excluded topics left out) with their latest note, the latest three kept recordings where the person spoke, with the summary, and when they last talked. Name chips in the list and the names on the admin page link there. 404 for a name without tasks and recordings. |
| **In plain words** | Under "Wartet auf" you see what others promised you. A click on a name shows everything about that person in one place before a meeting. |
| **Reasoning** | Owner request (features 1 and 2). In the summaries most next steps are other people's; the briefing is much stronger when it can say what someone still owes. One table with a kind keeps tags, history and undo without a second implementation. |
| **Rejected alternatives** | (A) a separate table for commitments: duplicates statuses, history, undo, tags and the MCP tools; (B) mixing waiting entries into the owner's list with a marker: the owner's list and counts would no longer show his own load; (C) a briefing generated by Claude on demand: slower and needs a call; the data is already structured. |
| **Status** | **FINAL** |

### D-039: Age counts from the conversation; "Wartet auf" filters by person (FINAL)

| | |
|---|---|
| **Date** | 2026-10-21 |
| **Decision** | The age of a task (list, briefing, MCP `age Nd`) counts from the start of its recording when it has one, otherwise from when it was written down (the earlier of the two). The "Wartet auf" view shows a bar of every person with open entries and their count (most first, plus "Alle"); a tap filters the list to that person, pre-fills "Von wem" in the add form and offers a link to the person's briefing. The sort orders "Neueste"/"Älteste" still use the time the entry was written down. "1 Tag" instead of "1 Tage". |
| **In plain words** | "Seit wann" now means since the conversation. In "Wartet auf" you pick a person at the top and see only what they owe you. |
| **Reasoning** | Owner request after the back-fill of D-038 showed every entry as zero days old. |
| **Rejected alternatives** | (A) setting `created_at` of back-filled entries to the recording time: rewrites when something was recorded and breaks the history; (B) a person drop-down: one more click than a visible bar with counts. |
| **Status** | **FINAL** |

### D-040: A task opened from a person leads back to that person (FINAL)

| | |
|---|---|
| **Date** | 2026-10-21 |
| **Decision** | Links to a task from a person's briefing, and from the task list filtered by a person, carry `?person=<name>`. The task page then shows the breadcrumb "Aufgaben / <Person> / <Task>", the person linking to the briefing. The name is only used if it is one of the task's people (any case, shown in the task's spelling); otherwise the usual breadcrumb ("Meine Aufgaben" or "Wartet auf") is shown. |
| **In plain words** | From a task you opened in Jochen's briefing, the path at the top leads back to Jochen. |
| **Reasoning** | Owner request. Checking the name against the task's people keeps the link from showing arbitrary text. |
| **Rejected alternatives** | (A) the browser's Referer: not sent reliably and lost after an autosave; (B) a session cookie for the last page: hidden state for a visible path. |
| **Status** | **FINAL** |

### D-041: Speaker rule 60 %, "Jetzt abholen", topic sorting and topic suggestions (FINAL)

| | |
|---|---|
| **Date** | 2026-10-23 |
| **Decision** | (1) Amends D-024: `MIN_SPEAKERS_NAMED_PERCENT` defaults to 60. In a one-on-one only the owner's label is often named by Plaud; 1 of 2 (50 %) no longer passes, both names are needed; 2 of 3 (67 %) passes. The 7-day limit stays. (2) "Jetzt abholen" on the recordings page writes a row to the new table `sync_requests` (`POST /sync`, CSRF as usual; refused while one is open or less than two minutes old). The app container polls every 20 seconds and moves the next run of the cycle job to now (`modify_job`), so the cycle runs in its own job and `max_instances=1` still prevents two at once. The page says when the owner last fetched by hand. (3) `/topics?sort=activity|name|open`: last activity (default), name, open tasks. (4) The "exclude a topic" field suggests the names of the topics not yet excluded (datalist); typing a new name still pre-excludes it. |
| **In plain words** | A one-on-one now waits until your counterpart is named too. "Jetzt abholen" fetches new recordings at once. Topics sort by date, name or open tasks, and excluding a topic suggests the names. |
| **Reasoning** | Owner requests. The web container has no Plaud credentials and must not run the cycle itself; a request row keeps the single cycle in the app container. Starting the Claude routine from the server would need an Anthropic credential on the server, so the button fetches from Plaud only. |
| **Rejected alternatives** | (A) running the import inside the web request: duplicates the Plaud client and could overlap with the scheduled cycle; (B) a lower poll interval: 20 seconds is fast enough and cheap; (C) triggering the Claude routine from the button: would store an account credential on the server. |
| **Status** | **FINAL** |

### D-042: A recording missing from Plaud's list is only taken as trashed if Plaud's trash holds it (FINAL)

| | |
|---|---|
| **Date** | 2026-10-23 |
| **Decision** | Amends D-019. Before, a stored recording that was not in Plaud's list (outside the trash) was marked as trashed without asking Plaud, and the permanent delete followed. Now the deletion reads Plaud's trash listing (`/file/simple/web?is_trash=1`, once per cycle and only if a recording is missing) and marks a missing recording as trashed only if it is there. If it is neither listed nor in the trash, or the trash cannot be read, it is held back and logged (`Not in the Plaud list and not in the trash, leaving it alone`). A recording marked as deleted that Plaud lists again outside the trash gets `trashed_at` and `deleted_at` cleared, so it is imported, verified and deleted again like any other. APScheduler's own run messages are logged at WARNING only, so the 20-second poll of D-041 does not flood the log. |
| **In plain words** | voice-vault no longer believes a recording is gone just because Plaud did not show it for a moment. Recordings wrongly marked as deleted are picked up again. |
| **Reasoning** | Owner report: after renaming a speaker, Plaud reprocessed the recording; it was apparently missing from the list for a cycle, was marked as trashed and "deleted" (the permanent delete call on a file outside the trash changed nothing), and stayed at Plaud while the UI said deleted. The later names could not come in. |
| **Rejected alternatives** | (A) asking the recording's detail for an is_trash flag: not known to be in the answer; the trash listing is the same, known endpoint; (B) never assuming trashed: a lost answer to our own trash call would leave the recording in Plaud's trash for ever. |
| **Status** | **FINAL** |

### D-043: Naming an unnamed speaker also replaces the placeholder in that recording's summary (FINAL)

| | |
|---|---|
| **Date** | 2026-10-23 |
| **Decision** | Amends D-034. "Zuweisen" under Verwaltung → Personen → "Ohne Namen" replaces the placeholder label (for example "Speaker 2") in the transcript passages and now also in the stored summary of the same recording, whole label only (`Speaker 2` does not touch `Speaker 20`), case as stored. Other recordings and Plaud are not changed. Renaming or deleting a real name (D-034) still leaves summaries alone. If Plaud later sends a changed summary, the re-import replaces the stored one as before. |
| **In plain words** | When you give "Speaker 2" a name, the summary of that recording shows the name too. |
| **Reasoning** | Owner request: Plaud writes the summary before the owner names the speakers, so it keeps "Speaker 2"; for recordings already deleted at Plaud it cannot be regenerated there. |
| **Rejected alternatives** | (A) replacing real names in summaries on rename: "Stefan" is part of "Stefan Heinze", free text would be corrupted; (B) asking Claude to rewrite the summary: changes more than the name. |
| **Status** | **FINAL** |

### D-044: The owner can exclude people (FINAL)

| | |
|---|---|
| **Date** | 2026-10-23 |
| **Decision** | New table `excluded_people` (name, unique, any case). Under Verwaltung → Personen each name has "Ausschließen"/"Zulassen", and a field excludes a name in advance. An excluded name is not offered as a tag or in the name field, is left out of the statistics' per-person chart, and is never set by Claude: `add_todo`/`update_todo` drop it (the answer says how many names were left out, without naming them), Claude cannot take off an excluded name the owner set himself, and `list_todos`/`get_topic` do not show excluded names to Claude. The owner can still type an excluded name onto a task. Existing entries, the briefing and the transcripts are not changed. |
| **In plain words** | Names you exclude are no longer suggested and Claude does not record them, for example your own name or people you do not want in the system. |
| **Reasoning** | Owner request; mirrors excluded topics. The owner's own name appears as a speaker in nearly every recording and is useless as a tag. |
| **Rejected alternatives** | (A) deleting the name (D-034): it would come back with the next recording; (B) refusing Claude's whole write when an excluded name is in it: a task for the owner must not get lost because of one name. |
| **Status** | **FINAL** |

### D-045: The "fetching" notice disappears when the fetch is through (FINAL)

| | |
|---|---|
| **Date** | 2026-10-23 |
| **Decision** | Amends D-041. `sync_requests` gets `finished_at` and `new_recordings` (migration `007_sync_requests_finished`). Every cycle, at its end and whatever its outcome, marks the requests it served as finished with the number of newly imported recordings. The recordings page shows "Abholen läuft …" while the last request is waiting or running; `/static/sync.js` asks `GET /sync/status` every three seconds and then hides the notice ("nothing new") or reloads the list (new recordings). A request not finished within ten minutes (app container down) counts as given up: the notice goes and a new request is allowed. While one runs, a second click does nothing. |
| **In plain words** | After "Jetzt abholen" the page says it is fetching, and the line goes away by itself when it is done; with new recordings the list reloads. |
| **Reasoning** | Owner request; the fixed notice of D-041 stayed after the fetch. |
| **Rejected alternatives** | (A) a fixed timer: the cycle takes from seconds to minutes; (B) server-sent events: more moving parts than polling a tiny status for a minute. |
| **Status** | **FINAL** |

### D-046: Recordings titled "#secret…" stay at Plaud only (FINAL)

| | |
|---|---|
| **Date** | 2026-10-23 |
| **Decision** | A recording whose Plaud title starts with `#secret` (any case, leading spaces ignored) is skipped by the import before any content is fetched: the title from the list is checked first, and the detail's title again (it may be newer after a rename; the detail is metadata only). Nothing is downloaded or stored, the log says `Secret recording, not downloading` with the id only. Such recordings are also left out of the set of listed recordings the deletion works on, so voice-vault never trashes or deletes them at Plaud, not even a recording discarded here earlier. A recording stored before it was renamed keeps its stored copy and is no longer updated or deleted at Plaud. |
| **In plain words** | Name a recording "#secret …" in Plaud and voice-vault leaves it alone: not fetched, not stored, not deleted there. |
| **Reasoning** | Owner request: some conversations must not leave Plaud. A title prefix is something the owner can set in the Plaud app in seconds. |
| **Rejected alternatives** | (A) a Plaud tag or folder: not read by this project, the title is; (B) importing and hiding it: the content would still be on the server. |
| **Status** | **FINAL** |

### D-047: Linking tasks by drawing a line, and a graph page (FINAL)

| | |
|---|---|
| **Date** | 2026-10-26 |
| **Decision** | New table `todo_links` (pair stored once, lower id first, unique) and module `task_links` (web UI only, like `topic_admin`: taking a link off deletes a row). Every task row in the list has a dot on the right; pressing it and drawing a line (pointer events: mouse, pen, finger) to another task and letting go links the two (`POST /todos/links`, JSON, CSRF as usual); a notice offers undo; Esc or letting go elsewhere cancels. The row shows "🔗 n". The task page lists the linked tasks with × to remove and a field with all open tasks ("#id title") to add one. Each link and unlink is written to both tasks' history (`linked`/`unlinked`, not undoable through the history; the × and the notice do that). `/todos/graph` draws the tasks and links with Cytoscape.js 3.30.2 (MIT, vendored in `static/vendor`, no eval, CSP unchanged): by default open tasks that have a link; toggles for finished tasks, topics as hub nodes (mind-map view) and tasks without links; nodes coloured by status, waiting entries dashed; clicking a node opens it, nodes can be dragged, clicking a line removes the link after a confirmation. Tasks of excluded topics are left out. At most 400 nodes. Claude has no tool for links. |
| **In plain words** | Draw a line from the dot of one task to another to link them; "Graph" shows them as a mind map, with finished tasks on request. |
| **Reasoning** | Owner request; the owner asked for a line instead of dragging the task itself. A drawn line reads as "connect these two" and works on touch screens, unlike HTML drag and drop. |
| **Rejected alternatives** | (A) HTML5 drag and drop of the whole row: no touch support, and it collides with text selection; (B) drawing links inside the graph: needs a further plug-in (edgehandles); the list is where the tasks are; (C) a hand-written force layout: a maintained library lays out and zooms better. |
| **Status** | **FINAL** |

### D-048: Links are visible in the task list; the graph is called "Mindmap" (FINAL)

| | |
|---|---|
| **Date** | 2026-10-26 |
| **Decision** | Amends D-047. Each task row lists its linked tasks under the title ("🔗 title, title, …", at most three, then "+n weitere"; finished ones struck through), each a link to that task; a link drawn in the list adds the entry at once, undo takes it off. Hovering a row draws curved lines from its dot to the linked tasks shown on the page and highlights them; scrolling or leaving the row hides them. The "🔗 n" counter is gone (`link_counts` replaced by `link_map`). The graph page and its link are named "Mindmap". |
| **In plain words** | In the list you see under each task what it is linked to, and pointing at a task draws the lines to its linked tasks. The graph is now called "Mindmap". |
| **Reasoning** | Owner request; a count alone did not show what is linked. Lines only on hover keep the list readable. |
| **Rejected alternatives** | (A) all lines drawn all the time: unreadable with more than a few links; (B) a separate column with the links: takes width from the titles. |
| **Status** | **FINAL** |

### D-049: Zoom buttons and linking on the Mindmap (FINAL)

| | |
|---|---|
| **Date** | 2026-10-26 |
| **Decision** | Amends D-047. The Mindmap has buttons at its top right edge: zoom in, zoom out, fit all, and 🔗 for a link mode. In link mode a tap on a task marks it and a dashed line follows the pointer (an invisible helper node that takes no clicks); a tap on a second task links the two through the same `POST /todos/links` as the list and draws the line; a tap on the same task, the button again or Esc ends it; topic nodes cannot be linked. A status line says what to do next and whether the link was made. Outside link mode a tap still opens the task and a tap on a line still offers to remove it. |
| **In plain words** | Zoom with + and − at the edge of the Mindmap; switch on 🔗, tap one task, then another, and they are linked. |
| **Reasoning** | Owner request. A mode with two taps works with mouse and finger and needs no further plug-in. |
| **Rejected alternatives** | (A) the edgehandles plug-in: one more vendored file for the same result; (B) Shift+drag: not possible on a touch screen. |
| **Status** | **FINAL** |

### D-050: A nicer Mindmap: fCoSE layout, readable sizes, focus on hover, legend, full screen (FINAL)

| | |
|---|---|
| **Date** | 2026-10-26 |
| **Decision** | Amends D-047/D-049. Layout with fCoSE 2.2.0 (cytoscape-fcose, with layout-base 2.0.1 and cose-base 2.2.0, all MIT, vendored, no eval): compact, without overlaps, separate groups packed side by side; topic edges shorter so tasks gather around their topic; built-in cose stays as fallback. Nodes: 13 px text (topics 15 px bold, filled accent), wrapped at 210 px; fill by status, an urgent or high priority as a red or orange rim, waiting entries dashed, finished ones faded. Links as soft curves, topic edges dotted. Pointing at a task fades everything not connected to it. The view starts fitted but never below zoom 0.75, so the text is readable; ⤢ shows everything. New ⛶ button: the browser's full screen (falls back to filling the window). A legend below explains colours and rims. Dotted background. |
| **In plain words** | The Mindmap is laid out tidily, the text is readable, colours and rims show status and priority, and ⛶ makes it full screen. |
| **Reasoning** | Owner feedback: layout, font sizes and the look were not good; full screen asked for. The built-in cose stretched graphs and let nodes overlap. |
| **Rejected alternatives** | (A) tuning the built-in cose only: overlaps remained; (B) a hierarchical layout (dagre): links between tasks have no direction; (C) fitting everything at start: text below 7 px on wider graphs. |
| **Status** | **FINAL** |

### D-051: "Wartet auf" entries are optional on the Mindmap, off by default (FINAL)

| | |
|---|---|
| **Date** | 2026-10-26 |
| **Decision** | Amends D-047. The Mindmap shows only the owner's own tasks by default. A fourth toggle "Wartet auf anzeigen" (`waiting=1`) adds the waiting entries (dashed); the legend lists them only then. Links to a waiting entry are hidden while it is off. |
| **In plain words** | The Mindmap shows your own tasks; switch on "Wartet auf anzeigen" to see what others owe you as well. |
| **Reasoning** | Owner request. |
| **Rejected alternatives** | (A) never showing them: some links are about who delivers what; (B) on by default: the owner asked to leave them out. |
| **Status** | **FINAL** |

### D-052: Hovering a task on the Mindmap highlights all tasks it reaches through links (FINAL)

| | |
|---|---|
| **Date** | 2026-10-26 |
| **Decision** | Amends D-050. On hover, a breadth-first search over link edges (not topic edges) gives every reachable task its distance: the task and its direct neighbours stay fully visible, the 2nd degree at 75 %, the 3rd and further at 50 %; everything not reachable fades to 12 %. The hovered task's own topic node and its topic edge stay visible. Hovering a topic node still highlights only that topic and its tasks. |
| **In plain words** | Pointing at a task now lights up the whole chain it is linked into, the further away the paler. |
| **Reasoning** | Owner request. Topics are not used as bridges: through a topic every task of it would count as connected. |
| **Rejected alternatives** | (A) a fixed depth limit: the owner asked for every degree; (B) the same strength for every degree: the direct links would no longer stand out. |
| **Status** | **FINAL** |
