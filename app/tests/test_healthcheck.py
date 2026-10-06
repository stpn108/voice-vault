"""Tests for the container health check."""
from sqlalchemy import create_engine

import healthcheck


def test_healthcheck_ok_when_database_answers():
    engine = create_engine("sqlite:///:memory:")
    assert healthcheck.check(engine) == 0


def test_healthcheck_fails_when_database_unreachable():
    engine = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")
    assert healthcheck.check(engine) == 1
