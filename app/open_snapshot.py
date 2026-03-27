from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar


SnapshotT = TypeVar("SnapshotT", bound="StoredOpenSnapshot")


@dataclass(frozen=True)
class StoredOpenSnapshot:
    opened_at: datetime | None
    ip_address: str | None
    user_agent: str | None
    country: str | None
    city: str | None
    proxy_type: str | None
    is_real_open: bool


def _stored_open_snapshot_fields(
    *,
    opened_at: datetime | None,
    ip_address: str | None,
    user_agent: str | None,
    country: str | None = None,
    city: str | None = None,
    proxy_type: str | None = None,
    is_real_open: bool | None,
) -> dict[str, datetime | str | bool | None]:
    return {
        "opened_at": opened_at,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "country": country,
        "city": city,
        "proxy_type": proxy_type,
        "is_real_open": bool(is_real_open),
    }


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
    return StoredOpenSnapshot(**_stored_open_snapshot_fields(
        opened_at=opened_at,
        ip_address=ip_address,
        user_agent=user_agent,
        country=country,
        city=city,
        proxy_type=proxy_type,
        is_real_open=is_real_open,
    ))


def build_open_snapshot(
    snapshot_type: type[SnapshotT],
    *,
    opened_at: datetime | None,
    ip_address: str | None,
    user_agent: str | None,
    country: str | None = None,
    city: str | None = None,
    proxy_type: str | None = None,
    is_real_open: bool | None,
    **extra_fields: object,
) -> SnapshotT:
    return snapshot_type(
        **extra_fields,
        **_stored_open_snapshot_fields(
            opened_at=opened_at,
            ip_address=ip_address,
            user_agent=user_agent,
            country=country,
            city=city,
            proxy_type=proxy_type,
            is_real_open=is_real_open,
        ),
    )
