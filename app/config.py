import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.lower() == "true"


def _get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Invalid DISPLAY_TIMEZONE '{name}'. "
            f"Use an IANA timezone name like 'America/New_York' or 'Europe/London'."
        ) from exc


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    api_key: str
    base_url: str
    dashboard_username: str
    dashboard_password: str
    display_timezone: ZoneInfo
    cookie_secure: bool
    followup_days: int
    trusted_proxy_cidrs: str
    smtp_server: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    notification_email: str
    maxmind_license_key: str


def load_settings() -> Settings:
    return Settings(
        database_url=_require_env("DATABASE_URL"),
        secret_key=_require_env("SECRET_KEY"),
        api_key=_require_env("API_KEY"),
        base_url=_require_env("BASE_URL").rstrip("/"),
        dashboard_username=_require_env("DASHBOARD_USERNAME"),
        dashboard_password=_require_env("DASHBOARD_PASSWORD"),
        display_timezone=_get_timezone(os.getenv("DISPLAY_TIMEZONE", "America/New_York")),
        cookie_secure=_get_bool("COOKIE_SECURE", True),
        followup_days=int(os.getenv("FOLLOWUP_DAYS", "3")),
        trusted_proxy_cidrs=os.getenv(
            "TRUSTED_PROXY_CIDRS",
            "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7",
        ),
        smtp_server=os.getenv("SMTP_SERVER", "smtp.gmail.com"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        notification_email=os.getenv("NOTIFICATION_EMAIL", ""),
        maxmind_license_key=os.getenv("MAXMIND_LICENSE_KEY", ""),
    )


settings = load_settings()
