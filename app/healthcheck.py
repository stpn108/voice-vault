"""
Container health check.

Exit code 0 when the database answers, 1 otherwise. Used by the Docker
healthcheck so an app whose database connection is gone is restarted
instead of silently doing nothing.
"""
import sys
import logging
from sqlalchemy import text

from database import engine
from utils import setup_logging

log = logging.getLogger(__name__)


def check(db_engine=engine) -> int:
    try:
        with db_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.error("Health check failed: database not reachable (%s)", exc)
        return 1
    return 0


if __name__ == "__main__":
    setup_logging()
    sys.exit(check())
