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
    ("min_age_minutes", 10080),  # D-014: 7 days
    ("permanent_delete_after_hours", 72),  # D-014: 3 days
    ("import_interval_minutes", 10),
    ("token_warn_days", 5),
])
def test_req_001_defaults(field, expected):
    assert getattr(load_config(), field) == expected


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("YES", True), ("false", False), ("", False)])
def test_delete_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("PLAUD_DELETE_ENABLED", value)
    assert load_config().delete_enabled is expected


def test_refresh_token_is_read_and_stripped(monkeypatch):
    monkeypatch.setenv("PLAUD_REFRESH_TOKEN", "  abc  ")
    assert load_config().plaud_refresh_token == "abc"


@pytest.mark.parametrize("value", [None, ""])
def test_redirect_uris_fall_back_to_the_claude_callback_when_unset_or_empty(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MCP_OAUTH_REDIRECT_URIS", raising=False)
    else:
        monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", value)
    assert load_config().mcp_oauth_redirect_uris == ("https://claude.ai/api/mcp/auth_callback",)


def test_redirect_uris_can_be_extended(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URIS", "https://claude.ai/api/mcp/auth_callback, http://localhost/callback")
    assert load_config().mcp_oauth_redirect_uris == ("https://claude.ai/api/mcp/auth_callback", "http://localhost/callback")
