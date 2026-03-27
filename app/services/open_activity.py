from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open
from ..open_classification import resolve_open_classification
from ..time_utils import ensure_utc


@dataclass(frozen=True)
class RealOpenEvent:
    tracked_email_id: str
    opened_at: datetime | None
    country: str | None = None
    city: str | None = None


@dataclass
class TrackRealOpenSummary:
    count: int = 0
    first_open_at: datetime | None = None
    last_open_at: datetime | None = None


async def load_real_open_events(
    db: AsyncSession,
    *,
    cutoff: datetime | None = None,
    track_ids: list[str] | None = None,
    include_location: bool = False,
) -> list[RealOpenEvent]:
    if track_ids == []:
        return []

    result = await db.execute(
        _build_real_open_query(
            cutoff=cutoff,
            track_ids=track_ids,
            include_location=include_location,
        )
    )

    real_opens: list[RealOpenEvent] = []
    for row in result:
        tracked_email_id, opened_at, is_real_open, proxy_type, ip_address, user_agent, *location = row
        if not _is_real_open(is_real_open, proxy_type, ip_address, user_agent):
            continue

        country = location[0] if include_location else None
        city = location[1] if include_location else None
        real_opens.append(
            RealOpenEvent(
                tracked_email_id=tracked_email_id,
                opened_at=ensure_utc(opened_at),
                country=country,
                city=city,
            )
        )

    return real_opens


async def load_real_open_summaries(
    db: AsyncSession,
    *,
    cutoff: datetime | None = None,
    track_ids: list[str] | None = None,
) -> dict[str, TrackRealOpenSummary]:
    if track_ids == []:
        return {}

    summaries: dict[str, TrackRealOpenSummary] = {}

    result = await db.execute(
        _build_real_open_query(cutoff=cutoff, track_ids=track_ids)
    )

    for tracked_email_id, opened_at, is_real_open, proxy_type, ip_address, user_agent in result:
        if not _is_real_open(is_real_open, proxy_type, ip_address, user_agent):
            continue

        summary = summaries.setdefault(tracked_email_id, TrackRealOpenSummary())
        summary.count += 1

        opened_at = ensure_utc(opened_at)
        if opened_at is None:
            continue

        if summary.first_open_at is None or opened_at < summary.first_open_at:
            summary.first_open_at = opened_at
        if summary.last_open_at is None or opened_at > summary.last_open_at:
            summary.last_open_at = opened_at

    return summaries


def _build_real_open_query(
    *,
    cutoff: datetime | None = None,
    track_ids: list[str] | None = None,
    include_location: bool = False,
):
    columns = [
        Open.tracked_email_id,
        Open.opened_at,
        Open.is_real_open,
        Open.proxy_type,
        Open.ip_address,
        Open.user_agent,
    ]
    if include_location:
        columns.extend([Open.country, Open.city])

    query = select(*columns)
    if cutoff is not None:
        query = query.where(Open.opened_at >= cutoff)
    if track_ids is not None:
        query = query.where(Open.tracked_email_id.in_(track_ids))
    return query


def _is_real_open(
    is_real_open: bool | None,
    proxy_type: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> bool:
    resolved_is_real_open, _ = resolve_open_classification(
        is_real_open=is_real_open,
        proxy_type=proxy_type,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return resolved_is_real_open
