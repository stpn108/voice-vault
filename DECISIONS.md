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
