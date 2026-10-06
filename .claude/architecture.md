# Architecture Rules

These rules keep the codebase in the same shape while the owner is not
reading it. They are deliberately boring. Deviating from them is an
architecture decision and needs a `DECISIONS.md` entry and the mentor.

## 1. Layers and dependency direction

```
Adapters   (HTTP handlers, bot handlers, CLI, scheduled jobs, templates)
    ↓ call
Services   (domain logic: rules, calculations, decisions)
    ↓ call
Data       (SQLAlchemy models, queries, migrations, external clients)
```

- Dependencies point downwards only. A service never imports an adapter.
  A model never imports a service.
- Adapters are thin: parse input, call one service function, format
  output. No business rule lives in a handler, a template or a job.
- Services receive plain data (ids, values, dataclasses) and return plain
  data. They never receive a request object, a Telegram update or a
  template context.
- External systems (LLM, payment, mail, other APIs) are wrapped in one
  module each under the data layer. The rest of the code never imports the
  vendor SDK directly.

## 2. Where does new code go

| You are adding … | It goes to … |
|------------------|--------------|
| A table or column | `database.py` model + numbered migration |
| A calculation, rule or threshold | A service module (`<domain>_service.py`), one function, unit-tested |
| An HTTP endpoint | `webapp.py` (or a router module under it); delegates to a service |
| A scheduled job | A job function next to the scheduler setup; delegates to a service |
| User-facing text | `strings.py` (DE + EN) |
| A date or time helper | `utils.py` |
| A new external system | `<vendor>_client.py`, one module, one config section |
| A one-off script | `scripts/` with a docstring saying when to run it |

When a module passes ~500 lines, split it by domain, not by layer.

## 3. Single source of truth

- Every domain number (a limit, a factor, a target, a threshold) is
  computed in exactly one function. Every consumer calls that function.
  Dashboards, reports, tests and the UI show the same number because they
  cannot compute a different one.
- Every domain term has one name (see `.claude/glossary.md`). Two names
  for one thing is a bug.
- Configuration comes from environment variables read in one place
  (`config` section at the top of the module or a `config.py`), never
  scattered `os.getenv()` calls with different defaults.

## 4. Data

- Every feature the owner might later want to measure writes a row with a
  `created_at` timestamp. If it is not in a table, it did not happen.
- Timestamps are stored in UTC, `DateTime(timezone=True)`. Dates use the
  local timezone via `utils.local_today()`.
- No string-built SQL. Parameterised queries or the ORM.
- Personal data is deletable: for every table holding user content, a
  deletion path exists (see `.claude/api-design.md` §5).

## 5. Boring technology

- No new framework, queue, cache, message bus or database without a
  decision. The default answer to "should we add Redis / Celery / a
  frontend framework" is no until measured need exists.
- Standard library before a package; a small package before a big one.
- Prefer a table and a scheduled job over a new service.

## 6. Feature flags and shadow mode

Any change that alters what users see for existing behaviour ships
behind a flag or in shadow mode first (compute, log, do not act), then is
switched on after the log shows it decides correctly. This is cheap; a
surprise for users is not.

## 7. Forbidden

- `datetime.now()` / `date.today()` outside `utils.py`
- Business logic in templates, handlers, jobs or migrations
- Global mutable state as a cache or a registry without a decision
- Catch-all `except Exception: pass`
- Hard-coded secrets, URLs, tenant ids, prices
- A second way to do something that already has one way
