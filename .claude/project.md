# Project Configuration

**voice-vault** is a small, security-conscious tool that regularly imports Plaud voice recordings from the owner's Plaud account into a database on the owner's own server and then deletes them at Plaud, so that conversation content stays at Plaud for as short a time as possible. The exported texts are processed further with Claude, not with Plaud.

**Project Path:** `/home/user/voice-vault` (server path: set as `DEPLOY_DIR`)

## Collaboration mode

**Mode:** `developer` (the owner is the sole decision maker and works with a terminal on the server; to be confirmed at read-back)

| Mode | Who develops | How a change is released | Rules |
|------|--------------|--------------------------|-------|
| `owner` | Claude. The owner defines requirements and never uses a terminal. | Claude opens a pull request; the owner merges it on GitHub; the server tests and deploys. | `collaboration.md` and `requirements.md` are binding |
| `developer` | A developer, with Claude. | Developer's choice: PR merge on GitHub, or `./merge-to-main.sh` / `./redeploy.sh` in a terminal. | `collaboration.md` §2 and §6 are recommendations |

## What this project is NOT

Scope creep is the most common way a small project drifts. List what is
explicitly out of scope, so a request that lands here is escalated instead
of built:

- NOT a Plaud replacement: the UI only shows, searches and deletes stored recordings (REQ-002)
- NOT a processing pipeline: no summarising, tagging or analysis of recordings here; that happens later with Claude
- NOT an audio archive: audio is exported only when an explicit flag is set, default off
- NOT built on plaud-tools: only a minimal own client with the endpoints needed
- NOT multi-account or multi-tenant: one Plaud account, one Google account

## People

| Role | Who | Responsibility |
|------|-----|----------------|
| **Owner** | Dennis Winter | Defines what the product does. Merges pull requests on GitHub. |
| **Mentor** | none (owner runs the server himself) | Reviews architecture and decisions, runs the server. |
| **Claude** | — | Implements. Follows `CLAUDE.md`, this directory and `DECISIONS.md`. |

## Stack

- **Language**: Python 3.12
- **Database**: PostgreSQL 16 (SQLAlchemy 2.x, numbered migrations)
- **Scheduling**: APScheduler (as in google-contacts-sync), every 10 minutes
- **Web**: FastAPI + Jinja2 + uvicorn, service `web`, viewing and cleanup UI (REQ-002, D-008)
- **Claude access**: MCP server, service `mcp`, bearer token or OAuth; reads recordings, writes only tasks (REQ-003, REQ-006, D-011, D-013)
- **External**: Plaud web API (unofficial, EU region)
- **LLM** (optional): see `.claude/llm.md`
- **Deployment**: Docker Compose on one server; GitHub Actions self-hosted runner runs `./redeploy.sh`
- **Tests**: pytest (+ ruff via `pytest --ruff`)

## Quick Reference

```
VERSION                 # App version (MAJOR.MINOR), source of truth
DECISIONS.md            # Architecture Decision Log of THIS project (ALWAYS maintain!)
RELEASE_NOTES.md        # User-facing change log (DE + EN)
Roadmap.md              # Scoped-but-not-scheduled ideas
README.md               # Operations, deployment, setup
requirements/           # One file per approved requirement (REQ-NNN-<slug>.md)
app/                    # Main code
├── main.py            # Application entrypoint (calls migrate_schema())
├── database.py        # SQLAlchemy models + MIGRATIONS runner
├── healthcheck.py     # Docker health probe (DB reachable?)
├── utils.py           # Timezone, datetime, logging helpers
├── strings.py         # i18n (DE/EN)
├── tests/             # pytest tests
├── templates/         # Jinja2 templates (if needed)
└── Dockerfile         # Container definition (COPY *.py, no per-file lines)
scripts/db-backup.sh    # pg_dump + retention, started by an Ofelia job (labels on db)
```

## Where to read what

| Question | File |
|----------|------|
| How do I turn an owner request into work? | `.claude/requirements.md` |
| How do I talk to the owner, what do I ask before doing? | `.claude/collaboration.md` |
| Where does new code go, what must never be mixed? | `.claude/architecture.md` |
| What does the owner call things, what does the code call them? | `.claude/glossary.md` |
| Why is the template built this way? | `.claude/template-decisions.md` |
| DB schema, migrations | `.claude/database.md` |
| Session handling, i18n, dates, logging | `.claude/code-patterns.md` |
| Tests | `.claude/testing.md` |
| Docker, deploy pipeline, backups, network posture | `.claude/deployment.md` |
| Several app instances, HAProxy, rolling deploy (only when needed) | `.claude/scaling.md` |
| Day-to-day operation (owner on GitHub, mentor in the terminal) | `.claude/operations.md` |
| HTTP API conventions | `.claude/api-design.md` |
| LLM integration | `.claude/llm.md` |
| Classic ML training | `.claude/machine-learning.md` |
| Release notes | `.claude/release-notes.md` |
| Before finishing any task | `.claude/checklist.md` |
