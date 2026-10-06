# Zenplate

A production-ready project template for Docker-based Python applications with
PostgreSQL, tracked migrations, automatic backups, auto-versioning, a
test-gated deploy pipeline, and development rules enforced via Claude Code.

Zenplate is built for projects where the **owner is not a developer**: the
owner defines requirements in plain language, Claude implements, a mentor
reviews decisions. The rules in `.claude/` keep the system stable while
nobody reads the code.

## Project Structure

```
VERSION                 # App version (MAJOR.MINOR), source of truth
SETUP.md                # Mentor how-to: repository, server, runner, owner onboarding
DECISIONS.md            # Architecture Decision Log of the project (starts empty)
RELEASE_NOTES.md        # User-facing change log (DE + EN)
Roadmap.md              # Ideas that are scoped but not scheduled
CLAUDE.md               # Development rules for Claude Code (immutable, hash-verified)
requirements/           # One file per requirement (REQ-NNN-<slug>.md), TEMPLATE.md
.claude/                # Project-specific Claude Code rules
├── project.md         # What the project is, what it is NOT, collaboration mode, people, doc map
├── template-decisions.md # Why the template is built this way (T-001..T-008)
├── collaboration.md   # How Claude works with a non-technical owner
├── requirements.md    # Requirement lifecycle: draft → approved → implemented
├── architecture.md    # Layers, where code goes, single source of truth, forbidden
├── glossary.md        # Owner's terms ↔ code names
├── operations.md      # Owner: release on GitHub. Mentor: logs, backups, restore
├── database.md        # Models, numbered migrations
├── code-patterns.md   # Dates, i18n, logging, external systems
├── testing.md         # Execution, determinism rules, safety tests
├── deployment.md      # Compose, deploy pipeline, runner setup, network posture
├── scaling.md         # app-1..N behind HAProxy, only when measured as necessary
├── api-design.md      # HTTP API conventions (if the project has an API)
├── llm.md             # LLM integration rules (if the project calls a model)
├── machine-learning.md# Classic ML training rules (if applicable)
├── release-notes.md   # What goes into RELEASE_NOTES.md
├── checklist.md       # Before finishing any task
└── skills/onboarding  # First-session interview that fills the placeholders
app/                    # Main code
├── main.py            # Application entrypoint
├── database.py        # SQLAlchemy models + MIGRATIONS runner
├── healthcheck.py     # Docker health probe
├── utils.py           # Timezone, datetime, logging helpers
├── strings.py         # i18n (DE/EN)
├── tests/             # pytest tests (incl. import smoke, migrations, healthcheck)
├── templates/         # Jinja2 templates
├── .dockerignore      # Keeps tests and tooling out of the image
└── Dockerfile         # COPY *.py — no per-file lines
scripts/db-backup.sh    # pg_dump + retention, run by the db-backup service
.github/workflows/
├── ci.yml             # Tests on every non-main push and PR (feedback)
├── deploy.yml         # Self-hosted runner: pull main, ./redeploy.sh (the gate)
└── version-bump.yml   # Minor bump on every merge to main
```

---

## Starting a new project

1. Create a repository from this template. Keep it **private** (the deploy
   runner executes repository code).
2. Open Claude Code in the repository. `project.md` carries an
   "onboarding not done" banner, so Claude's first action is the
   `onboarding` skill: it asks the owner what the project is, whether the
   owner develops (`developer` mode) or defines requirements only (`owner`
   mode), and fills `project.md`, `glossary.md` and the first requirement.
3. Mentor, once per project: server checkout, `.env`, runner, branch
   rules. Step by step in `SETUP.md`.
4. From then on, in `owner` mode: owner describes, Claude writes a
   requirement and reads it back, owner confirms, Claude implements on a
   branch and opens a pull request, owner presses Merge on GitHub, the
   server tests and deploys. The owner never opens a terminal.

---

## Environment Variables

See `.env.example`. Required: `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `DATABASE_URL`. Optional: `TZ`, `LOG_LEVEL`,
`PORTS_PREFIX`, `COMPOSE_PROJECT_NAME`, `BACKUP_INTERVAL`,
`BACKUP_RETENTION_DAYS`.

---

## Deployment

### Automatic (owner mode)

Merge the pull request on GitHub. GitHub Actions bumps the version, then
the self-hosted runner on the server runs `./redeploy.sh`: tests in the
real test container, build, restart, health and version verification.
Red = nothing deployed. Details and owner instructions: `.claude/operations.md`.

### Manual (developer mode, or hotfix by the mentor)

```bash
./merge-to-main.sh                 # branch → main from the terminal
./redeploy.sh                      # tests → build → deploy → verify
```

### Everyday commands (mentor)

```bash
./version.sh                       # repo version vs running app
docker compose logs -f app         # logs
docker compose ps                  # health of all services
ls -lh volumes/backups/            # database backups
```

---

## Versioning

**Source of Truth**: `VERSION` in the repo root (format: `MAJOR.MINOR`).

- **Minor bump**: automatic via GitHub Action on every merge to `main`
- **Major bump**: manual, only for breaking changes, and only after asking
  the owner. Change `VERSION` in the branch before merging.

---

## Testing

```bash
cd app && pytest                   # all tests + ruff
pytest --testmon                   # only affected tests
pytest --cov=. --cov-report=html   # coverage
```

The `app-tests` service runs the suite continuously in watch mode:

```bash
docker compose up -d app-tests && docker compose logs -f app-tests
```

---

## Docker Services

| Service | Purpose | Port |
|---------|---------|------|
| **db** | PostgreSQL 16 | internal |
| **db-backup** | `pg_dump` every `BACKUP_INTERVAL` into `volumes/backups/` | — |
| **app** | Main application, health-checked | `127.0.0.1:${PORTS_PREFIX}010:8000` |
| **app-tests** | Continuous test runner | — |
