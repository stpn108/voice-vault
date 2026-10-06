# Database Rules

## Schema Changes

**ALWAYS in `database.py`:**

1. **New table**: Add a Model class. `migrate_schema()` creates it via
   `Base.metadata.create_all()`.
2. **New column** (or index, constraint, data fix): extend the Model AND add
   a numbered migration:
   ```python
   def _migrate_002_example_priority(conn):
       conn.execute(sqltext(
           "ALTER TABLE IF EXISTS example_items "
           "ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0;"
       ))

   MIGRATIONS = [
       ("001_…", _migrate_001_…),
       ("002_example_priority", _migrate_002_example_priority),
   ]
   ```
3. **Idempotent**: `IF NOT EXISTS` / `IF EXISTS` on every DDL statement, so
   a re-run on a database that already has the change cannot fail.
4. **Numbered, append-only**: migrations are never renumbered, reordered or
   deleted once deployed. A wrong migration is fixed by a new one.
5. **No `conn.commit()` inside a migration.** The runner holds one
   transaction and an advisory lock; a crash mid-way leaves the database
   untouched and nothing is recorded in `schema_migrations`.
6. **Data-changing migrations** (UPDATE/DELETE) need owner approval first
   (`.claude/collaboration.md` §3) and a test with representative rows.

## How the runner works

`migrate_schema()` runs once at startup: takes a Postgres advisory lock
(so several containers starting at once do not race), `create_all()`,
creates `schema_migrations` if missing, applies every version not yet
recorded, records it. On SQLite (tests) the same path runs without the lock.
Tests for the runner live in `tests/test_migrations.py`.

## Session Handling

```python
from sqlalchemy.orm import Session
from database import engine

with Session(engine) as s:
    # DB operations
    s.commit()  # EXPLICIT commit!
```

## Column naming

- `created_at` (UTC, `DateTime(timezone=True)`, `server_default=func.now()`)
  on every table.
- Booleans as `is_<state>` / `has_<thing>`; never a bare adjective.
- Foreign keys as `<table_singular>_id`.
- Names come from `.claude/glossary.md`. Check it before inventing one.

## Personal data

Every table that stores user content is listed in `.claude/glossary.md`
under "Units and formats" or a "Personal data" table, together with the
deletion path. If the project has an API, see `.claude/api-design.md` §5.
