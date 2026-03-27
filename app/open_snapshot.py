from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StoredOpenSnapshot:
    opened_at: datetime | None
    ip_address: str | None
    user_agent: str | None
    country: str | None
    city: str | None
    proxy_type: str | None
    is_real_open: bool


def build_stored_open_snapshot(
    *,
    opened_at: datetime | None,
    ip_address: str | None,
    user_agent: str | None,
    country: str | None = None,
    city: str | None = None,
    proxy_type: str | None = None,
    is_real_open: bool | None,
) -> StoredOpenSnapshot:
    return StoredOpenSnapshot(
        opened_at=opened_at,
        ip_address=ip_address,
        user_agent=user_agent,
        country=country,
        city=city,
        proxy_type=proxy_type,
        is_real_open=bool(is_real_open),
    )
