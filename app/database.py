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
    Text, Boolean, ForeignKey, func, text as sqltext,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, Session

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# MODELS (names: .claude/glossary.md)
# ---------------------------------------------------------------------------
class Recording(Base):
    """One Plaud recording (Aufnahme) with its imported content (Import)."""
    __tablename__ = "recordings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plaud_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_plaud_processed: Mapped[bool] = mapped_column(Boolean, default=True)
    last_changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    verified_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trashed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    discarded_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Segment(Base):
    """One utterance of a recording's transcript."""
    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(255), default="")
    start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OAuthCode(Base):
    """A one-time OAuth authorization code of the MCP server (stored as a hash, 60 s lifetime)."""
    __tablename__ = "oauth_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OAuthToken(Base):
    """An OAuth access or refresh token of the MCP server. Only the SHA-256 hash is stored."""
    __tablename__ = "oauth_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PlaudSession(Base):
    """The one current Plaud token pair (single row, id=1). Secret: never log or export."""
    __tablename__ = "plaud_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seed_fingerprint: Mapped[str] = mapped_column(String(16), default="")
    refreshed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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

def _migrate_001_recordings_discarded_at(conn):
    # Fresh databases get the column from create_all(); only Postgres needs the ALTER.
    if conn.dialect.name == "postgresql":
        conn.execute(sqltext(
            "ALTER TABLE IF EXISTS recordings "
            "ADD COLUMN IF NOT EXISTS discarded_at TIMESTAMP WITH TIME ZONE;"
        ))


MIGRATIONS: list = [
    ("001_recordings_discarded_at", _migrate_001_recordings_discarded_at),
]

# Advisory lock key: serialises schema setup when several replicas start at
# once (concurrent create_all() on a fresh Postgres collides on pg_type).
_SCHEMA_LOCK_KEY = 0x56564C54  # "VVLT"


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
