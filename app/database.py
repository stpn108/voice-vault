"""
SQLAlchemy models and schema migrations.

Rules (see .claude/database.md):
  - New table:  Add Model class here -> created via Base.metadata.create_all()
  - New column: Add to Model AND add a numbered migration to MIGRATIONS
  - Migrations are idempotent (IF NOT EXISTS / IF EXISTS) and NEVER commit;
    the runner owns the transaction.
"""
import os
import logging
import datetime as dt
from typing import Optional
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    Text, Boolean, func, text as sqltext,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, Session

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# EXAMPLE MODEL — replace with your own
# ---------------------------------------------------------------------------
class ExampleItem(Base):
    __tablename__ = "example_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# SCHEMA MIGRATIONS
# ---------------------------------------------------------------------------
# Each migration is a function taking a Connection. It runs exactly once per
# database, tracked in the schema_migrations table. Rules:
#   - Numbered, ascending, never renumbered or removed once deployed.
#   - Idempotent DDL (IF NOT EXISTS / IF EXISTS) so a re-run cannot fail.
#   - No conn.commit() inside a migration: the runner holds one transaction
#     so a crash mid-way leaves the database untouched.
#
# Example:
#
#     def _migrate_001_example_priority(conn):
#         conn.execute(sqltext(
#             "ALTER TABLE IF EXISTS example_items "
#             "ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0;"
#         ))
#
#     MIGRATIONS = [
#         ("001_example_priority", _migrate_001_example_priority),
#     ]

MIGRATIONS: list = [
    # ("001_<name>", _migrate_001_<name>),
]

# Advisory lock key: serialises schema setup when several replicas start at
# once (concurrent create_all() on a fresh Postgres collides on pg_type).
_SCHEMA_LOCK_KEY = 0x5A454E31  # "ZEN1"


def migrate_schema(db_engine=None):
    """
    Create tables and apply pending migrations. Called once at startup.
    Safe to run concurrently from several containers (Postgres advisory lock)
    and safe to re-run (tracked versions are skipped).
    """
    db_engine = db_engine or engine
    with db_engine.begin() as conn:
        if db_engine.dialect.name == "postgresql":
            conn.execute(sqltext("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})

        Base.metadata.create_all(conn)
        conn.execute(sqltext(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(64) PRIMARY KEY, "
            "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        applied = {
            row[0] for row in
            conn.execute(sqltext("SELECT version FROM schema_migrations")).fetchall()
        }
        for version, func_ in MIGRATIONS:
            if version in applied:
                continue
            func_(conn)
            conn.execute(
                sqltext("INSERT INTO schema_migrations (version) VALUES (:v) "
                        "ON CONFLICT (version) DO NOTHING"),
                {"v": version},
            )
            log.info("Applied migration %s", version)
