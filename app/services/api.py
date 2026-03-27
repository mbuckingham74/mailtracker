import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open, TrackedEmail
from ..open_snapshot import StoredOpenSnapshot, build_open_snapshot

RECENT_REAL_OPENS_LIMIT = 50
RECENT_OPEN_BATCH_SIZE = 200


@dataclass(frozen=True)
class TrackSnapshot:
    id: str
    recipient: str | None
    subject: str | None
    notes: str | None
    message_group_id: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class RecentOpenSnapshot:
    id: int
    opened_at: datetime | None
    country: str | None
    city: str | None
    ip_address: str | None
    user_agent: str | None


@dataclass(frozen=True)
class RecentOpenTrackSnapshot:
    id: str
    recipient: str | None
    subject: str | None


@dataclass(frozen=True)
class OpenSnapshot(StoredOpenSnapshot):
    id: int
    referer: str | None


async def list_tracks(db: AsyncSession) -> list[tuple[TrackSnapshot, int]]:
    open_counts = (
        select(
            Open.tracked_email_id.label("track_id"),
            func.count(Open.id).label("open_count"),
        )
        .group_by(Open.tracked_email_id)
        .subquery()
    )

    result = await db.execute(
        select(
            TrackedEmail.id,
            TrackedEmail.recipient,
            TrackedEmail.subject,
            TrackedEmail.notes,
            TrackedEmail.message_group_id,
            TrackedEmail.created_at,
            func.coalesce(open_counts.c.open_count, 0).label("open_count"),
        )
        .outerjoin(open_counts, open_counts.c.track_id == TrackedEmail.id)
        .order_by(TrackedEmail.created_at.desc())
    )
    return [
        (
            TrackSnapshot(
                id=track_id,
                recipient=recipient,
                subject=subject,
                notes=notes,
                message_group_id=message_group_id,
                created_at=created_at,
            ),
            int(open_count or 0),
        )
        for track_id, recipient, subject, notes, message_group_id, created_at, open_count in result
    ]


async def create_track(
    db: AsyncSession,
    *,
    recipient: str | None,
    subject: str | None,
    notes: str | None,
    message_group_id: str | None,
) -> TrackedEmail:
    new_track = TrackedEmail(
        id=str(uuid.uuid4()),
        recipient=recipient,
        subject=subject,
        notes=notes,
        message_group_id=message_group_id,
    )

    db.add(new_track)
    await db.commit()
    await db.refresh(new_track)
    return new_track


async def get_track_with_opens(
    db: AsyncSession,
    track_id: str,
) -> tuple[TrackSnapshot, list[OpenSnapshot]]:
    track = await _get_track_or_404(db, track_id)
    opens = await list_track_opens(db, track_id)
    return track, opens


async def list_track_opens(db: AsyncSession, track_id: str) -> list[OpenSnapshot]:
    result = await db.execute(
        select(
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
        .where(Open.tracked_email_id == track_id)
        .order_by(Open.opened_at.desc(), Open.id.desc())
    )
    opens: list[OpenSnapshot] = []
    for (
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
        opens.append(
            build_open_snapshot(
                OpenSnapshot,
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
    return opens


async def delete_track(db: AsyncSession, track_id: str) -> None:
    await _get_track_or_404(db, track_id)
    await db.execute(delete(TrackedEmail).where(TrackedEmail.id == track_id))
    await db.commit()


async def get_stats(db: AsyncSession) -> dict[str, int]:
    tracks_result = await db.execute(select(func.count(TrackedEmail.id)))
    opens_result = await db.execute(select(func.count(Open.id)))
    with_opens_result = await db.execute(
        select(func.count(func.distinct(Open.tracked_email_id)))
    )

    return {
        "total_tracks": tracks_result.scalar() or 0,
        "total_opens": opens_result.scalar() or 0,
        "tracks_with_opens": with_opens_result.scalar() or 0,
    }


async def get_recent_real_opens(
    db: AsyncSession,
    since_dt: datetime | None,
) -> list[tuple[RecentOpenSnapshot, RecentOpenTrackSnapshot]]:
    recent_opens: list[tuple[RecentOpenSnapshot, RecentOpenTrackSnapshot]] = []
    cursor_opened_at: datetime | None = None
    cursor_open_id: int | None = None

    while len(recent_opens) < RECENT_REAL_OPENS_LIMIT:
        query = (
            select(
                Open.id,
                Open.opened_at,
                Open.country,
                Open.city,
                Open.ip_address,
                Open.user_agent,
                TrackedEmail.id,
                TrackedEmail.recipient,
                TrackedEmail.subject,
            )
            .join(TrackedEmail, Open.tracked_email_id == TrackedEmail.id)
            .where(Open.is_real_open.is_(True))
            .order_by(Open.opened_at.desc(), Open.id.desc())
            .limit(RECENT_OPEN_BATCH_SIZE)
        )

        if since_dt is not None:
            query = query.where(Open.opened_at > since_dt)
        if cursor_opened_at is not None and cursor_open_id is not None:
            query = query.where(
                or_(
                    Open.opened_at < cursor_opened_at,
                    and_(Open.opened_at == cursor_opened_at, Open.id < cursor_open_id),
                )
            )

        result = await db.execute(query)
        rows_fetched = 0
        last_open_id: int | None = None
        last_opened_at: datetime | None = None

        for row in result:
            rows_fetched += 1
            (
                open_id,
                opened_at,
                country,
                city,
                ip_address,
                user_agent,
                track_id,
                recipient,
                subject,
            ) = row
            last_open_id = open_id
            last_opened_at = opened_at

            recent_opens.append((
                RecentOpenSnapshot(
                    id=open_id,
                    opened_at=opened_at,
                    country=country,
                    city=city,
                    ip_address=ip_address,
                    user_agent=user_agent,
                ),
                RecentOpenTrackSnapshot(
                    id=track_id,
                    recipient=recipient,
                    subject=subject,
                ),
            ))
            if len(recent_opens) >= RECENT_REAL_OPENS_LIMIT:
                break

        if rows_fetched == 0:
            break
        if rows_fetched < RECENT_OPEN_BATCH_SIZE:
            break

        if last_opened_at is None:
            break
        cursor_opened_at = last_opened_at
        cursor_open_id = last_open_id

    return recent_opens


async def _get_track_or_404(db: AsyncSession, track_id: str) -> TrackSnapshot:
    result = await db.execute(
        select(
            TrackedEmail.id,
            TrackedEmail.recipient,
            TrackedEmail.subject,
            TrackedEmail.notes,
            TrackedEmail.message_group_id,
            TrackedEmail.created_at,
        ).where(TrackedEmail.id == track_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return TrackSnapshot(
        id=row[0],
        recipient=row[1],
        subject=row[2],
        notes=row[3],
        message_group_id=row[4],
        created_at=row[5],
    )
