# Change Checklist

Before reporting any task as done, verify:

**Requirement & scope**
- [ ] Behaviour change? → `requirements/REQ-NNN-*.md` exists and is `APPROVED`
- [ ] Stayed inside that requirement? Anything extra → Roadmap or new requirement
- [ ] New domain term? → `.claude/glossary.md` updated first
- [ ] Architecture/design decision? → `DECISIONS.md`, immediately
- [ ] Touches a `FINAL` decision or `architecture.md` rule? → stop, escalate

**Code**
- [ ] New DB table? → Model in `database.py`
- [ ] New DB column / index / data fix? → Model + numbered migration in `MIGRATIONS`
- [ ] Migration idempotent, no `commit()` inside?
- [ ] New non-Python asset the container needs (template, data file)? → `COPY` in `Dockerfile`
- [ ] New env variable? → `docker-compose.yml` + `.env.example` + README
- [ ] New external dependency or optional extra? → `requirements.txt`, pinned
- [ ] User-facing text? → `strings.py` (DE + EN)
- [ ] Time-based logic? → `utils.local_today()` / `utils.now_utc()`, NOT `datetime.now()`
- [ ] Domain number computed? → in exactly one function
- [ ] Feature with data? → which table logs it, `created_at` present?
- [ ] Secrets? → environment variable, NEVER in code or git

**Verification**
- [ ] Tests for every acceptance criterion, `cd app && pytest` green (includes ruff)
- [ ] No test weakened, skipped or deleted to get green
- [ ] Breaking change? → **Ask first**, then bump `VERSION` to next major

**Communication**
- [ ] User-visible change? → entry in `RELEASE_NOTES.md` (see `.claude/release-notes.md`)
- [ ] Pull request opened with the template; `Tests` check green; never ask the owner to merge red
- [ ] Requirement set to `IMPLEMENTED` with version and tests (after merge)
- [ ] Owner report: what changed for you / what is open / what I need (incl. PR link)
