from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open
from ..proxy_detection import detect_proxy_type
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

    columns = [Open.tracked_email_id, Open.opened_at, Open.ip_address, Open.user_agent]
    if include_location:
        columns.extend([Open.country, Open.city])

    query = select(*columns)
    if cutoff is not None:
        query = query.where(Open.opened_at >= cutoff)
    if track_ids is not None:
        query = query.where(Open.tracked_email_id.in_(track_ids))

    result = await db.execute(query)

    real_opens: list[RealOpenEvent] = []
    for row in result.all():
        tracked_email_id, opened_at, ip_address, user_agent, *location = row
        if detect_proxy_type(ip_address or "", user_agent or "") is not None:
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
    summaries: dict[str, TrackRealOpenSummary] = {}

    for open_event in await load_real_open_events(db, cutoff=cutoff, track_ids=track_ids):
        summary = summaries.setdefault(open_event.tracked_email_id, TrackRealOpenSummary())
        summary.count += 1

        if open_event.opened_at is None:
            continue

        if summary.first_open_at is None or open_event.opened_at < summary.first_open_at:
            summary.first_open_at = open_event.opened_at
        if summary.last_open_at is None or open_event.opened_at > summary.last_open_at:
            summary.last_open_at = open_event.opened_at

    return summaries
