from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open
from ..open_snapshot import StoredOpenSnapshot, build_open_snapshot
from ..time_utils import ensure_utc


OpenSortOrder = Literal["asc", "desc"]


@dataclass(frozen=True)
class TrackOpenRecord(StoredOpenSnapshot):
    tracked_email_id: str
    id: int
    referer: str | None


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


@dataclass
class TrackOpenSummary:
    open_count: int = 0
    real_open_count: int = 0
    first_open: datetime | None = None
    first_real_open: datetime | None = None
    first_proxy_open: datetime | None = None
    first_proxy_type: str | None = None


async def load_track_open_records(
    db: AsyncSession,
    track_id: str,
    *,
    order: OpenSortOrder = "asc",
) -> list[TrackOpenRecord]:
    return (
        await load_track_open_records_map(db, [track_id], order=order)
    ).get(track_id, [])


async def load_track_open_records_map(
    db: AsyncSession,
    track_ids: list[str],
    *,
    order: OpenSortOrder = "asc",
) -> dict[str, list[TrackOpenRecord]]:
    if not track_ids:
        return {}

    result = await db.execute(
        _build_track_open_records_query(track_ids=track_ids, order=order)
    )

    opens_by_track_id: dict[str, list[TrackOpenRecord]] = defaultdict(list)
    for (
        tracked_email_id,
        open_id,
        opened_at,
        is_real_open,
        proxy_type,
        ip_address,
        user_agent,
        referer,
        country,
        city,
    ) in result:
        opens_by_track_id[tracked_email_id].append(
            build_open_snapshot(
                TrackOpenRecord,
                tracked_email_id=tracked_email_id,
                id=open_id,
                referer=referer,
                opened_at=opened_at,
                ip_address=ip_address,
                user_agent=user_agent,
                country=country,
                city=city,
                proxy_type=proxy_type,
                is_real_open=is_real_open,
            )
        )

    return dict(opens_by_track_id)


async def load_track_open_summaries(
    db: AsyncSession,
    *,
    track_ids: list[str],
) -> dict[str, TrackOpenSummary]:
    if not track_ids:
        return {}

    result = await db.execute(
        _build_track_open_summary_query(track_ids=track_ids)
    )

    summaries: dict[str, TrackOpenSummary] = {}
    for tracked_email_id, opened_at, is_real_open, proxy_type in result:
        summary = summaries.setdefault(tracked_email_id, TrackOpenSummary())
        _accumulate_track_open_summary(
            summary,
            opened_at=opened_at,
            is_real_open=is_real_open,
            proxy_type=proxy_type,
        )

    return summaries


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
        tracked_email_id, opened_at, *location = row
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

    for tracked_email_id, opened_at in result:
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


def _build_track_open_records_query(
    *,
    track_ids: list[str],
    order: OpenSortOrder,
):
    return (
        select(
            Open.tracked_email_id,
            Open.id,
            Open.opened_at,
            Open.is_real_open,
            Open.proxy_type,
            Open.ip_address,
            Open.user_agent,
            Open.referer,
            Open.country,
            Open.city,
        )
        .where(Open.tracked_email_id.in_(track_ids))
        .order_by(*_build_track_open_order_by(order))
    )


def _build_track_open_summary_query(
    *,
    track_ids: list[str],
):
    return (
        select(
            Open.tracked_email_id,
            Open.opened_at,
            Open.is_real_open,
            Open.proxy_type,
        )
        .where(Open.tracked_email_id.in_(track_ids))
        .order_by(*_build_track_open_order_by("asc"))
    )


def _build_track_open_order_by(order: OpenSortOrder) -> tuple:
    if order == "desc":
        return (
            Open.tracked_email_id.asc(),
            Open.opened_at.desc(),
            Open.id.desc(),
        )

    return (
        Open.tracked_email_id.asc(),
        Open.opened_at.asc(),
        Open.id.asc(),
    )


def _accumulate_track_open_summary(
    summary: TrackOpenSummary,
    *,
    opened_at: datetime | None,
    is_real_open: bool | None,
    proxy_type: str | None,
) -> None:
    summary.open_count += 1
    if summary.first_open is None:
        summary.first_open = opened_at

    if not is_real_open:
        if summary.first_proxy_open is None and proxy_type is not None:
            summary.first_proxy_open = opened_at
            summary.first_proxy_type = proxy_type
        return

    summary.real_open_count += 1
    if summary.first_real_open is None:
        summary.first_real_open = opened_at


def _build_real_open_query(
    *,
    cutoff: datetime | None = None,
    track_ids: list[str] | None = None,
    include_location: bool = False,
):
    columns = [
        Open.tracked_email_id,
        Open.opened_at,
    ]
    if include_location:
        columns.extend([Open.country, Open.city])

    query = select(*columns).where(Open.is_real_open.is_(True))
    if cutoff is not None:
        query = query.where(Open.opened_at >= cutoff)
    if track_ids is not None:
        query = query.where(Open.tracked_email_id.in_(track_ids))
    return query
