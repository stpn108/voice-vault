# Code Patterns

## Date/Time

```python
from utils import today_str, now_utc, local_today
# today_str()   → "2026-01-17" (local date, 04:00 day boundary)
# now_utc()     → timezone-aware datetime in UTC (for timestamps)
# local_today() → date in the local timezone (for "which day is it" logic)
```

- **ALWAYS** `utils.now_utc()` for timestamps, `utils.local_today()` for
  date logic. **NEVER** `datetime.now()` / `date.today()` outside `utils.py`.
- The day starts at **04:00** local time: entries between midnight and
  04:00 belong to the previous day. Change this only via a decision.

| Component | Configuration |
|-----------|---------------|
| `utils.LOCAL_TZ` | `tz.gettz(os.getenv("TZ", "Europe/Berlin"))` |
| Docker | `TZ` from `.env`, default `Europe/Berlin` |
| Scheduler (if any) | Pass `LOCAL_TZ` explicitly; schedulers default to UTC |

### Common timezone mistakes

1. **Scheduler runs in UTC instead of local** → the scheduler was not given `LOCAL_TZ`
2. **Naive vs. aware datetimes** → `now_utc()` for timestamps, `local_today()` for dates
3. **04:00 boundary ignored in tests** → seed test data relative to
   `local_today()`, never `date.today()` (see `.claude/testing.md`)

## i18n

```python
from strings import get_text
message = get_text("key", lang, var=value)
```

- Every user-facing string lives in `strings.py` with both `de` and `en`.
- Keys are `snake_case`, grouped by feature prefix (`reminder_…`, `error_…`).
- No string concatenation for sentences; use `{placeholders}`.

## Logging

```python
import logging
log = logging.getLogger(__name__)

log.info("Reminder sent user_id=%s type=%s", user_id, reminder_type)
log.warning("LLM call slow user_id=%s latency_ms=%s", user_id, ms)
log.error("Payment failed user_id=%s reason=%s", user_id, exc)
```

- `setup_logging()` once in `main()`; per-module loggers everywhere else.
- Every log line carries the identifiers needed to find the case again
  (user id, request id, job name). "Something went wrong" is not a log line.
- Levels: DEBUG detail, INFO operational events, WARNING recoverable,
  ERROR failed. No `print()` in production code.

## Single source of truth for domain numbers

```python
# service module
def daily_budget_kcal(profile) -> int:
    """The ONLY place the daily budget is computed."""
    ...

# everywhere else (UI, report, job, test)
budget = daily_budget_kcal(profile)
```

A domain number that is computed in two places will disagree eventually.
Dashboards and reports call the same function the product calls.

## External systems

One module per external system (`<vendor>_client.py`) that owns the SDK
import, the credentials from env, timeouts and retries. Callers get plain
Python values back and never see vendor exceptions.
