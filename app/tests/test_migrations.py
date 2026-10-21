"""Tests for the schema migration runner."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import database


@pytest.fixture
def memory_engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _applied_versions(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {r[0] for r in rows}


def test_migrate_schema_creates_tables_and_tracking_table(memory_engine, monkeypatch):
    monkeypatch.setattr(database, "MIGRATIONS", [])

    database.migrate_schema(memory_engine)

    with memory_engine.connect() as conn:
        conn.execute(text("SELECT id FROM recordings"))
    assert _applied_versions(memory_engine) == set()


def test_migrate_schema_applies_each_migration_once(memory_engine, monkeypatch):
    calls = []

    def _migrate_001(conn):
        calls.append("001")
        conn.execute(text("ALTER TABLE recordings ADD COLUMN priority INTEGER DEFAULT 0"))

    def _migrate_002(conn):
        calls.append("002")

    monkeypatch.setattr(database, "MIGRATIONS", [
        ("001_priority", _migrate_001),
        ("002_noop", _migrate_002),
    ])

    database.migrate_schema(memory_engine)
    database.migrate_schema(memory_engine)  # second run must be a no-op

    assert calls == ["001", "002"]
    assert _applied_versions(memory_engine) == {"001_priority", "002_noop"}
    with memory_engine.connect() as conn:
        conn.execute(text("SELECT priority FROM recordings"))


def test_migrate_schema_skips_already_applied_and_runs_new_ones(memory_engine, monkeypatch):
    calls = []
    monkeypatch.setattr(database, "MIGRATIONS", [
        ("001_first", lambda conn: calls.append("001")),
    ])
    database.migrate_schema(memory_engine)

    monkeypatch.setattr(database, "MIGRATIONS", [
        ("001_first", lambda conn: calls.append("001")),
        ("002_second", lambda conn: calls.append("002")),
    ])
    database.migrate_schema(memory_engine)

    assert calls == ["001", "002"]


def test_failed_migration_is_rolled_back_and_not_recorded(memory_engine, monkeypatch):
    def _broken(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(database, "MIGRATIONS", [("001_broken", _broken)])

    with pytest.raises(RuntimeError):
        database.migrate_schema(memory_engine)

    monkeypatch.setattr(database, "MIGRATIONS", [])
    database.migrate_schema(memory_engine)
    assert "001_broken" not in _applied_versions(memory_engine)


def test_d028_status_column_fits_in_progress():
    from database import MIGRATIONS, Todo
    assert "004_todos_status_length" in [name for name, _ in MIGRATIONS]
    assert Todo.__table__.c.status.type.length >= len("in_progress")


def test_d030_people_migration_is_registered():
    from database import MIGRATIONS, Todo
    assert "005_todos_people" in [name for name, _ in MIGRATIONS] and "people" in Todo.__table__.c



def test_d038_kind_migration_is_registered():
    from database import MIGRATIONS, Todo
    assert "006_todos_kind" in [name for name, _ in MIGRATIONS] and "kind" in Todo.__table__.c
