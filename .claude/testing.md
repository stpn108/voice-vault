# Testing

## Execution

```bash
cd app && pytest                 # all tests + ruff (addopts = --ruff)
pytest tests/test_example.py -v  # one file
pytest --testmon                 # only tests affected by the change
pytest --cov=. --cov-report=html # coverage
```

`pytest` includes ruff (`pytest-ruff`, configured in `pyproject.toml`).
An undefined name or a syntax error fails the suite, not production.

## Fixtures (`conftest.py`)

- `db_session` — fresh in-memory SQLite session with all tables.
- Fixtures live in `conftest.py` only. A test file that needs a different
  setup documents why at the top of the file.

## Test categories

| Category | Files | External? | When |
|----------|-------|-----------|------|
| **Unit** | `test_*.py` | No | Always (`pytest`, CI, deploy) |
| **Integration** | `test_*_integration.py`, `@pytest.mark.integration` | Maybe | Explicit request |
| **LLM live** | `test_*_llm.py` | Yes, paid | Manually by the owner/mentor, never by Claude (see `llm.md`) |

## Always-on safety tests (do not remove)

- `test_imports.py` — every top-level module imports. Catches NameError,
  circular imports, missing dependencies.
- `test_migrations.py` — the migration runner applies once, skips applied,
  rolls back on failure.
- `test_healthcheck.py` — the container health probe.

## Determinism rules

Tests must pass at any wall-clock time, on any machine, with no service
running. Lessons from ZenTallyBot (D-109, D-123, D-158):

1. **Dates**: seed relative to `utils.local_today()` (04:00 boundary), never
   `date.today()`. Or pin time with `freezegun`.
2. **No live services**: a unit test never needs Postgres, Redis, the
   network or an API key. Stub at the client module boundary; an autouse
   fixture in `conftest.py` should make accidental live calls fail loudly.
3. **No shared state**: each test builds its own rows. No ordering
   assumptions.
4. **No sleeping**: time-based behaviour is tested by injecting the clock.

## Conventions

- Filename `test_<feature>.py`; test name says the behaviour, and for
  requirement tests the id: `test_req_012_reminder_not_sent_twice`.
- `@pytest.mark.parametrize` instead of loops; a loop hides which case
  failed.
- One assertion topic per test. A failing test name should tell the owner
  what broke without opening the file.
- Every bug fix adds the test that would have caught it, with a one-line
  comment naming the bug.
- Prefer real inputs (from logs, from the owner) over invented ones.
- Async tests: `@pytest.mark.asyncio` (auto mode is on).

## Never

- Never fix a failing test by weakening the assertion, adding `xfail`,
  `skip`, or deleting it. Fix the code or take the requirement back to the
  owner.
- Never test the same behaviour in two files.
- Never let a test depend on a specific date, locale or hostname.
