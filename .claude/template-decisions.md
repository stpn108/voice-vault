# Template Decisions (inherited from Zenplate)

These decisions are baked into the template. They explain why the
scaffolding looks the way it does and are **FINAL** for every project built
from Zenplate. They are not part of the project's own `DECISIONS.md`, which
starts empty at D-001. To deviate from one of them, add a project decision
that supersedes it and involve the mentor.

### T-001: Dockerfile copies `*.py`, tests excluded via `.dockerignore`

| | |
|---|---|
| **Decision** | `app/Dockerfile` uses `COPY *.py .`; `app/.dockerignore` excludes tests, caches and tooling. Non-Python assets still get explicit `COPY` lines. `tests/test_imports.py` imports every top-level module. |
| **In plain words** | A new code file can no longer be forgotten in the container build, which used to crash the app only after deployment. |
| **Reasoning** | ZenTallyBot D-029: every forgotten `COPY` produced a runtime ImportError in production. A template for non-technical owners must remove error classes rather than add checklist items. `CLAUDE.md` Rule 11 is satisfied automatically by the glob. |
| **Rejected alternatives** | (A) Per-file COPY lines plus a checklist item — the error class stays; (B) `COPY . .` — ships tests and tooling into the image |

### T-002: Numbered migrations with `schema_migrations` tracking and advisory lock, no Alembic

| | |
|---|---|
| **Decision** | `database.migrate_schema()` takes a Postgres advisory lock, runs `create_all()`, then applies every `(version, func)` in `MIGRATIONS` not yet recorded in `schema_migrations`, in one transaction. Migrations are append-only, idempotent and never `commit()`. SQLite runs the same path without the lock. |
| **In plain words** | Database changes are applied exactly once, in order, and a failed change is undone completely. Several app copies can start at once without breaking each other. |
| **Reasoning** | ZenTallyBot D-031 and D-110. Forbidding `commit()` inside migrations removes the need for a non-committing connection proxy. |
| **Rejected alternatives** | (A) Alembic — autogenerate drift, a second CLI and config surface; (B) inline `ALTER … IF NOT EXISTS` without tracking — no record, no rollback |

### T-003: Database backups as a Compose service running `pg_dump` in a loop

| | |
|---|---|
| **Decision** | Service `db-backup` runs `scripts/db-backup.sh` every `BACKUP_INTERVAL` (default 4h) into `./volumes/backups`, pruning after `BACKUP_RETENTION_DAYS` (default 7). |
| **In plain words** | The database is saved automatically every few hours, so a mistake can be undone by going back to an earlier copy. |
| **Reasoning** | ZenTallyBot depends on an ofelia daemon shared with another project on the host; invisible in the repo. A loop inside Compose has no external dependency and shows up in `docker compose ps`. |
| **Rejected alternatives** | (A) ofelia labels — needs a daemon outside this repo; (B) host cron — invisible, lost on server rebuild |

### T-004: Container health = database reachable; deploy verifies the running commit

| | |
|---|---|
| **Decision** | `app` has a Docker healthcheck running `python healthcheck.py` (`SELECT 1`). `redeploy.sh` waits for `healthy` and verifies the running `GIT_COMMIT` equals the built one; either failing aborts with exit 1. |
| **In plain words** | After every deployment the script checks that the new version is actually running and can talk to the database. |
| **Reasoning** | `CLAUDE.md` Rule 11 requires health checks. ZenTallyBot D-156: a service ran a stale image for weeks because the check only warned. A DB probe works for any app type. |
| **Rejected alternatives** | (A) HTTP `/health` — only for web apps; (B) `pgrep` — proves a process exists, not that it works; (C) warning instead of failure |

### T-005: Written, approved requirement before code (owner mode)

| | |
|---|---|
| **Decision** | In `owner` mode every behaviour change starts as `requirements/REQ-NNN-<slug>.md`, drafted by Claude, read back in plain language, set to `APPROVED` by the owner before implementation. Acceptance criteria map 1:1 to tests. In `developer` mode the requirement file is recommended, not required. |
| **In plain words** | Nothing gets built until it is written down in a way the owner has read and confirmed. |
| **Reasoning** | A non-developer owner cannot compare "what was meant" with "what was built" in code. The written requirement is the only place where that comparison is possible. |
| **Rejected alternatives** | (A) GitHub issues — outside the repo, not versioned with the code; (B) one `REQUIREMENTS.md` — unbounded, no per-feature status; (C) no gate |

### T-006: Repository content in English, conversation in the owner's language

| | |
|---|---|
| **Decision** | Files are English (`CLAUDE.md` Rule 1); the verbatim "Owner ask" keeps the owner's language; Claude talks to the owner in their language; `RELEASE_NOTES.md` is DE + EN; every decision has an "In plain words" row. |
| **In plain words** | Project files are in English so they stay consistent; talking to you happens in your language. |
| **Reasoning** | Rule 1 is immutable. ZenTallyBot's log became bilingual over time; a plain-words row avoids a second document. |
| **Rejected alternatives** | (A) owner-language files — violates Rule 1; (B) two versions of each file — drift |

### T-007: Release = merge a pull request on GitHub; the server tests and deploys automatically

| | |
|---|---|
| **Decision** | Claude opens a pull request for every change. `ci.yml` shows tests green or red on the PR. The owner merges on GitHub. On `main`, `version-bump.yml` bumps `VERSION`; when it completes, `deploy.yml` runs on a self-hosted runner on the server: `git pull` in `DEPLOY_DIR`, then `./redeploy.sh` (tests in the real test container, deploy only on green, verify health and commit). Deploy starts on completion of the bump run regardless of its outcome, and the bump treats a rejected push as a warning, so a version number can never block a deploy. Branch rules on `main` block force pushes and deletions only; a required check or PR requirement would only stop the version number from advancing. The gate is the server-side test run, not the Merge button. One runner per repository (personal accounts have no org runners); that is also what isolates several projects on one server. In `developer` mode `./merge-to-main.sh` and `./redeploy.sh` remain available as a terminal path. |
| **In plain words** | You release by clicking "Merge" on GitHub. The server then tests the new version itself and only switches over if every test passes. You never need a terminal. |
| **Reasoning** | The owner must not need a console. GitHub is the one interface they have: PR, checks, merge button, Actions log. ZenTallyBot's webhook receiver (D-039/D-041) was deprecated: custom HTTP server, systemd, token auth, lock. A self-hosted runner is outbound-only, needs no secrets in the repo, and reuses `redeploy.sh` so manual and automatic deploys are identical. Triggering on completion of the bump workflow gives exactly one deploy per merge with the final `VERSION`. |
| **Rejected alternatives** | (A) webhook receiver — the deprecated design; (B) SSH from GitHub-hosted runners — private key in secrets, inbound SSH; (C) cron `git pull` on the server — no visibility for the owner; (D) tests only on GitHub-hosted runners — not on the server; (E) owner runs scripts in a terminal — the requirement this template exists to remove |

### T-008: Release notes as `RELEASE_NOTES.md` (Markdown, DE + EN)

| | |
|---|---|
| **Decision** | User-facing changes go to `RELEASE_NOTES.md` in the repo root, newest first, one DE and one EN bullet per change, grouped by `VERSION`. |
| **In plain words** | One file lists, in everyday language, what changed for users in each version. |
| **Reasoning** | The checklist demanded release notes but no file existed. Markdown is readable on GitHub without tooling. |
| **Rejected alternatives** | (A) JSON like ZenTallyBot — needs an in-app renderer; (B) git releases — outside the repo, not bilingual. Switch to JSON if the project renders notes in-app. |
