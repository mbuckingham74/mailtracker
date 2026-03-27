import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..client_ip import get_client_ip
from ..database import Open, TrackedEmail
from ..geoip import lookup_ip
from ..notifications import (
    is_email_notifications_enabled,
    send_hot_conversation_notification,
    send_open_notification,
    send_revived_conversation_notification,
)
from ..time_utils import ensure_utc
from ..open_classification import classify_open
from .open_activity import load_real_open_summaries

logger = logging.getLogger(__name__)

MIN_OPEN_DELAY_SECONDS = 5


@dataclass(frozen=True)
class TrackingSnapshot:
    recipient: str | None
    subject: str | None
    created_at: datetime | None
    notified_at: datetime | None
    hot_notified_at: datetime | None
    revived_notified_at: datetime | None


async def record_pixel_open(
    db: AsyncSession,
    tracking_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> None:
    result = await db.execute(
        select(
            TrackedEmail.recipient,
            TrackedEmail.subject,
            TrackedEmail.created_at,
            TrackedEmail.notified_at,
            TrackedEmail.hot_notified_at,
            TrackedEmail.revived_notified_at,
        ).where(TrackedEmail.id == tracking_id)
    )
    row = result.one_or_none()
    if row is None:
        return
    tracked_email = TrackingSnapshot(
        recipient=row[0],
        subject=row[1],
        created_at=row[2],
        notified_at=row[3],
        hot_notified_at=row[4],
        revived_notified_at=row[5],
    )

    now = datetime.now(timezone.utc)
    created_at = ensure_utc(tracked_email.created_at)
    if created_at is None:
        return

    if (now - created_at).total_seconds() < MIN_OPEN_DELAY_SECONDS:
        return

    ip_address = get_client_ip(request) or (request.client.host if request.client else "")
    user_agent = request.headers.get("User-Agent", "")
    referer = request.headers.get("Referer", "")
    is_real_open, proxy_type = classify_open(ip_address, user_agent)
    country, city = lookup_ip(ip_address)

    email_recipient = tracked_email.recipient or "Unknown"
    email_subject = tracked_email.subject or "(no subject)"
    notifications_enabled = is_email_notifications_enabled()
    should_notify = (
        tracked_email.notified_at is None
        and notifications_enabled
        and is_real_open
    )

    db.add(
        Open(
            tracked_email_id=tracking_id,
            opened_at=now,
            ip_address=ip_address,
            user_agent=user_agent,
            referer=referer,
            country=country,
            city=city,
            proxy_type=proxy_type,
            is_real_open=is_real_open,
        )
    )

    update_values: dict[str, datetime] = {}
    if should_notify:
        update_values["notified_at"] = now

    should_send_hot_conversation = False
    hot_open_count = 0
    should_send_revived_conversation = False
    days_since_first_real_open = 0

    if is_real_open and notifications_enabled:
        await db.flush()

        if tracked_email.hot_notified_at is None:
            hot_open_count = await _load_recent_real_open_count(db, tracking_id, now)
            if hot_open_count >= 3:
                update_values["hot_notified_at"] = now
                should_send_hot_conversation = True

        if tracked_email.revived_notified_at is None:
            first_real_open_at = await _load_first_real_open_at(db, tracking_id)
            if first_real_open_at is not None:
                days_since_first_real_open = (now - first_real_open_at).days
                if days_since_first_real_open >= 14:
                    update_values["revived_notified_at"] = now
                    should_send_revived_conversation = True

    if update_values:
        await db.execute(
            update(TrackedEmail)
            .where(TrackedEmail.id == tracking_id)
            .values(**update_values)
        )

    await db.commit()

    if should_notify:
        background_tasks.add_task(
            send_open_notification,
            recipient=email_recipient,
            subject=email_subject,
            opened_at=now,
            country=country,
            city=city,
            track_id=tracking_id,
            sent_at=created_at,
        )

    if should_send_hot_conversation:
        background_tasks.add_task(
            send_hot_conversation_notification,
            recipient=email_recipient,
            subject=email_subject,
            open_count=hot_open_count,
            track_id=tracking_id,
        )

    if should_send_revived_conversation:
        background_tasks.add_task(
            send_revived_conversation_notification,
            recipient=email_recipient,
            subject=email_subject,
            days_since_first_open=days_since_first_real_open,
            track_id=tracking_id,
        )


async def _load_recent_real_open_count(
    db: AsyncSession,
    tracking_id: str,
    now: datetime,
) -> int:
    twenty_four_hours_ago = now - timedelta(hours=24)
    real_open_summary = (
        await load_real_open_summaries(
            db,
            cutoff=twenty_four_hours_ago,
            track_ids=[tracking_id],
        )
    ).get(tracking_id)
    return real_open_summary.count if real_open_summary else 0


async def _load_first_real_open_at(
    db: AsyncSession,
    tracking_id: str,
) -> datetime | None:
    real_open_summary = (
        await load_real_open_summaries(db, track_ids=[tracking_id])
    ).get(tracking_id)
    return real_open_summary.first_open_at if real_open_summary else None
