import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open, TrackedEmail
from ..proxy_detection import detect_proxy_type

RECENT_REAL_OPENS_LIMIT = 50
RECENT_OPEN_BATCH_SIZE = 200


async def list_tracks(db: AsyncSession) -> list[tuple[TrackedEmail, int]]:
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
            TrackedEmail,
            func.coalesce(open_counts.c.open_count, 0).label("open_count"),
        )
        .outerjoin(open_counts, open_counts.c.track_id == TrackedEmail.id)
        .order_by(TrackedEmail.created_at.desc())
    )
    return [(track, int(open_count or 0)) for track, open_count in result.all()]


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


async def get_track_with_opens(db: AsyncSession, track_id: str) -> tuple[TrackedEmail, list[Open]]:
    track = await _get_track_or_404(db, track_id)
    opens = await list_track_opens(db, track_id)
    return track, opens


async def list_track_opens(db: AsyncSession, track_id: str) -> list[Open]:
    result = await db.execute(
        select(Open)
        .where(Open.tracked_email_id == track_id)
        .order_by(Open.opened_at.desc(), Open.id.desc())
    )
    return result.scalars().all()


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
) -> list[tuple[Open, TrackedEmail]]:
    recent_opens: list[tuple[Open, TrackedEmail]] = []
    cursor_opened_at: datetime | None = None
    cursor_open_id: int | None = None

    while len(recent_opens) < RECENT_REAL_OPENS_LIMIT:
        query = (
            select(Open, TrackedEmail)
            .join(TrackedEmail, Open.tracked_email_id == TrackedEmail.id)
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
        rows = result.all()
        if not rows:
            break

        for open_record, tracked_email in rows:
            proxy_type = detect_proxy_type(open_record.ip_address or "", open_record.user_agent or "")
            if proxy_type is not None:
                continue

            recent_opens.append((open_record, tracked_email))
            if len(recent_opens) >= RECENT_REAL_OPENS_LIMIT:
                break

        if len(rows) < RECENT_OPEN_BATCH_SIZE:
            break

        last_open = rows[-1][0]
        if last_open.opened_at is None:
            break
        cursor_opened_at = last_open.opened_at
        cursor_open_id = last_open.id

    return recent_opens


async def _get_track_or_404(db: AsyncSession, track_id: str) -> TrackedEmail:
    result = await db.execute(
        select(TrackedEmail).where(TrackedEmail.id == track_id)
    )
    track = result.scalar_one_or_none()
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return track
