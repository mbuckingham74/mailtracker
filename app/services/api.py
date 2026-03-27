import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Open, TrackedEmail
from .open_activity import (
    RecentRealOpenRecord,
    TrackOpenRecord,
    load_latest_real_open_record,
    load_track_open_records,
)


@dataclass(frozen=True)
class TrackSnapshot:
    id: str
    recipient: str | None
    subject: str | None
    notes: str | None
    message_group_id: str | None
    created_at: datetime | None


def _build_track_snapshot(
    *,
    track_id: str,
    recipient: str | None,
    subject: str | None,
    notes: str | None,
    message_group_id: str | None,
    created_at: datetime | None,
) -> TrackSnapshot:
    return TrackSnapshot(
        id=track_id,
        recipient=recipient,
        subject=subject,
        notes=notes,
        message_group_id=message_group_id,
        created_at=created_at,
    )


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
            _build_track_snapshot(
                track_id=track_id,
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
        created_at=datetime.now(timezone.utc),
    )

    db.add(new_track)
    await db.commit()
    await db.refresh(new_track)
    return new_track


async def get_track_with_opens(
    db: AsyncSession,
    track_id: str,
) -> tuple[TrackSnapshot, list[TrackOpenRecord]]:
    track = await _get_track_or_404(db, track_id)
    opens = await list_track_opens(db, track_id)
    return track, opens


async def list_track_opens(db: AsyncSession, track_id: str) -> list[TrackOpenRecord]:
    return await load_track_open_records(db, track_id, order="desc")


async def delete_track(db: AsyncSession, track_id: str) -> None:
    await _get_track_or_404(db, track_id)
    await db.execute(delete(TrackedEmail).where(TrackedEmail.id == track_id))
    await db.commit()


def _build_latest_real_open_payload(
    open_record: RecentRealOpenRecord | None,
) -> dict[str, object] | None:
    if open_record is None:
        return None

    return {
        "open_id": open_record.id,
        "opened_at": open_record.opened_at,
        "recipient": open_record.recipient,
        "subject": open_record.subject,
        "country": open_record.country,
        "city": open_record.city,
    }


async def get_stats(db: AsyncSession) -> dict[str, object]:
    tracks_result = await db.execute(select(func.count(TrackedEmail.id)))
    opens_result = await db.execute(select(func.count(Open.id)))
    with_opens_result = await db.execute(
        select(func.count(func.distinct(Open.tracked_email_id)))
    )
    latest_real_open = await load_latest_real_open_record(db)

    return {
        "total_tracks": tracks_result.scalar() or 0,
        "total_opens": opens_result.scalar() or 0,
        "tracks_with_opens": with_opens_result.scalar() or 0,
        "latest_real_open": _build_latest_real_open_payload(latest_real_open),
    }


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
    return _build_track_snapshot(
        track_id=row[0],
        recipient=row[1],
        subject=row[2],
        notes=row[3],
        message_group_id=row[4],
        created_at=row[5],
    )
