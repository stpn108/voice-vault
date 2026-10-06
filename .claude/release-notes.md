# Release Notes

## File

`RELEASE_NOTES.md` in the repo root, newest version first. The owner reads
it on GitHub; the app may render it (e.g. behind a version label).

## Format

```markdown
## v1.4 — 2026-03-15

- **DE:** Erinnerungen kommen jetzt auch am Wochenende.
- **EN:** Reminders are now also sent on weekends.
```

Both languages for every bullet. Several changes in one version → several
bullet pairs under the same heading. Version = current `VERSION` file.

## When to add an entry

Whenever a change is **visible or relevant to end users**:

- New features or UI changes
- Behaviour changes (different calculation, changed defaults)
- Performance improvements the user would notice
- Bug fixes that affected users

Do **NOT** add entries for:

- Refactoring, cleanup, restructuring
- CI/CD, deployment, infrastructure
- Tooling, tests, dependency updates
- Migrations without user-facing effect
- Logging, monitoring

## How to write entries

- **Audience:** end users, non-technical. What changed for them, not how.
- **Length:** one short sentence per bullet.
- **Tone:** neutral, factual. No marketing, no emojis, no internal names.
- Good: "Daily view now shows the correct calorie total."
- Bad: "Fixed SQL aggregation bug in day_totals()."
