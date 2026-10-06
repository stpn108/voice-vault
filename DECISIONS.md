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
