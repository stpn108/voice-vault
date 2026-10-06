"""Configuration defaults (REQ-001): safe by default."""
import pytest

from config import load_config

ENV_KEYS = ["PLAUD_DELETE_ENABLED", "STORE_AUDIO", "MIN_AGE_MINUTES", "IMPORT_INTERVAL_MINUTES",
            "PERMANENT_DELETE_AFTER_HOURS", "TOKEN_WARN_DAYS"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("field,expected", [
    ("delete_enabled", False),   # shadow mode until switched on
    ("store_audio", False),      # REQ-001 criterion 2
    ("min_age_minutes", 15),
    ("import_interval_minutes", 10),
    ("token_warn_days", 5),
])
def test_req_001_defaults(field, expected):
    assert getattr(load_config(), field) == expected


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("YES", True), ("false", False), ("", False)])
def test_delete_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("PLAUD_DELETE_ENABLED", value)
    assert load_config().delete_enabled is expected
