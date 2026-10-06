"""All environment configuration, read in one place (.claude/architecture.md §3)."""
import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


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
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    mail_from: str
    mail_to: str
    ui_lang: str
    ui_allowed_hosts: tuple


def load_config() -> Config:
    return Config(
        plaud_token=os.getenv("PLAUD_TOKEN", "").strip(),
        plaud_refresh_token=os.getenv("PLAUD_REFRESH_TOKEN", "").strip(),
        plaud_api_base=os.getenv("PLAUD_API_BASE", "https://api-euc1.plaud.ai").rstrip("/"),
        import_interval_minutes=int(os.getenv("IMPORT_INTERVAL_MINUTES", "10")),
        min_age_minutes=int(os.getenv("MIN_AGE_MINUTES", "1440")),
        stability_minutes=int(os.getenv("STABILITY_MINUTES", "10")),
        permanent_delete_after_hours=int(os.getenv("PERMANENT_DELETE_AFTER_HOURS", "24")),
        delete_enabled=_bool("PLAUD_DELETE_ENABLED", False),
        store_audio=_bool("STORE_AUDIO", False),
        token_warn_days=int(os.getenv("TOKEN_WARN_DAYS", "5")),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        mail_from=os.getenv("MAIL_FROM", ""),
        mail_to=os.getenv("MAIL_TO", ""),
        ui_lang=os.getenv("UI_LANG", "de").strip() or "de",
        ui_allowed_hosts=tuple(h.strip() for h in os.getenv("UI_ALLOWED_HOSTS", "").split(",") if h.strip()),
    )
