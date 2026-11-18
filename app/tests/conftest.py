"""
Pytest configuration and shared fixtures.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set required env vars before imports (prevents crashes on module load)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import database

# Register pytest-asyncio plugin
pytest_plugins = ['pytest_asyncio']


@pytest.fixture
def db_session():
    """Creates a temporary in-memory DB session with all tables."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.Base.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def session_factory():
    """Factory for sessions on one shared in-memory database (all tables)."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.Base.metadata.create_all(test_engine)
    return lambda: Session(test_engine)


@pytest.fixture(autouse=True)
def no_live_claude_api(monkeypatch):
    """A unit test never calls the Claude API (.claude/llm.md §8): building a real SDK client fails loudly."""
    import llm_client

    def refuse(api_key):
        raise AssertionError("a unit test tried to build a live Claude API client")

    monkeypatch.setattr(llm_client, "_new_sdk_client", refuse)
