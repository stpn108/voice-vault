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
