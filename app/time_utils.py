from datetime import datetime, timezone

from .config import settings


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime | None) -> datetime | None:
    dt = ensure_utc(dt)
    if dt is None:
        return None
    return dt.astimezone(settings.display_timezone)


def format_duration_hours(hours: float | None) -> str:
    if hours is None:
        return "N/A"
    if hours < 1:
        return f"{int(hours * 60)} min"
    if hours < 24:
        return f"{hours:.1f} hrs"
    return f"{hours / 24:.1f} days"


def format_time_ago(dt: datetime | None, now: datetime) -> str:
    dt = ensure_utc(dt)
    if dt is None:
        return "Never"

    delta = now - dt
    seconds = delta.total_seconds()

    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} min ago" if minutes == 1 else f"{minutes} mins ago"
    if seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hr ago" if hours == 1 else f"{hours} hrs ago"
    if seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day ago" if days == 1 else f"{days} days ago"
    if seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks} week ago" if weeks == 1 else f"{weeks} weeks ago"
    return to_local(dt).strftime("%b %d, %Y")
