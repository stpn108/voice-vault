"""All environment configuration, read in one place (.claude/architecture.md §3)."""
import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class ConfigError(RuntimeError):
    """The configuration is not safe or complete enough to start a service."""


def _csv(name: str, default: str = "") -> tuple:
    raw = os.getenv(name) or default  # compose passes unset variables on as empty strings
    return tuple(v.strip() for v in raw.split(",") if v.strip())


def host_name(value: str) -> str:
    """A bare lowercase host name from a host, host:port or full URL (a pasted https://host/path works)."""
    value = value.strip().lower()
    if "://" in value:
        value = urlparse(value).netloc
    value = value.split("/")[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _int(name: str, default: int, minimum: int) -> int:
    """A whole number of at least `minimum`. Empty means the default. A 0 or negative value would
    silently switch a safety gate off, so it stops the start instead."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got '{raw[:20]}'") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


def _hosts(name: str) -> tuple:
    return tuple(h for h in (host_name(v) for v in _csv(name)) if h)


@dataclass(frozen=True)
class Config:
    plaud_token: str
    plaud_refresh_token: str
    plaud_api_base: str
    import_interval_minutes: int
    min_age_minutes: int
    stability_minutes: int
    permanent_delete_after_hours: int
    delete_enabled: bool
    store_audio: bool
    token_warn_days: int
    max_trash_per_cycle: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    mail_from: str
    mail_to: str
    ui_lang: str
    ui_allowed_hosts: tuple
    mcp_tokens: tuple
    mcp_allowed_hosts: tuple
    mcp_allowed_origins: tuple
    mcp_public_url: str
    mcp_oauth_client_id: str
    mcp_oauth_client_secret: str
    mcp_oauth_password: str
    mcp_oauth_redirect_uris: tuple


def load_config() -> Config:
    return Config(
        plaud_token=os.getenv("PLAUD_TOKEN", "").strip(),
        plaud_refresh_token=os.getenv("PLAUD_REFRESH_TOKEN", "").strip(),
        plaud_api_base=os.getenv("PLAUD_API_BASE", "https://api-euc1.plaud.ai").rstrip("/"),
        import_interval_minutes=_int("IMPORT_INTERVAL_MINUTES", 10, 1),
        min_age_minutes=_int("MIN_AGE_MINUTES", 10080, 1),
        stability_minutes=_int("STABILITY_MINUTES", 10, 1),
        permanent_delete_after_hours=_int("PERMANENT_DELETE_AFTER_HOURS", 72, 1),
        delete_enabled=_bool("PLAUD_DELETE_ENABLED", False),
        store_audio=_bool("STORE_AUDIO", False),
        token_warn_days=_int("TOKEN_WARN_DAYS", 5, 0),
        max_trash_per_cycle=_int("MAX_TRASH_PER_CYCLE", 20, 1),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        mail_from=os.getenv("MAIL_FROM", ""),
        mail_to=os.getenv("MAIL_TO", ""),
        ui_lang=os.getenv("UI_LANG", "de").strip() or "de",
        ui_allowed_hosts=tuple(h.strip() for h in os.getenv("UI_ALLOWED_HOSTS", "").split(",") if h.strip()),
        mcp_tokens=_csv("MCP_TOKENS"),
        mcp_allowed_hosts=_hosts("MCP_ALLOWED_HOSTS"),
        mcp_allowed_origins=_csv("MCP_ALLOWED_ORIGINS"),
        mcp_public_url=os.getenv("MCP_PUBLIC_URL", "").strip().rstrip("/"),
        mcp_oauth_client_id=os.getenv("MCP_OAUTH_CLIENT_ID", "").strip(),
        mcp_oauth_client_secret=os.getenv("MCP_OAUTH_CLIENT_SECRET", "").strip(),
        mcp_oauth_password=os.getenv("MCP_OAUTH_PASSWORD", ""),
        mcp_oauth_redirect_uris=_csv("MCP_OAUTH_REDIRECT_URIS", "https://claude.ai/api/mcp/auth_callback"),
    )
