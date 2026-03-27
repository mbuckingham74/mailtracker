import logging
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Request
from sqlalchemy import func, select
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
from ..proxy_detection import detect_proxy_type
from ..time_utils import ensure_utc

logger = logging.getLogger(__name__)

MIN_OPEN_DELAY_SECONDS = 5


async def record_pixel_open(
    db: AsyncSession,
    tracking_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> None:
    result = await db.execute(
        select(TrackedEmail).where(TrackedEmail.id == tracking_id)
    )
    tracked_email = result.scalar_one_or_none()
    if not tracked_email:
        return

    now = datetime.now(timezone.utc)
    created_at = ensure_utc(tracked_email.created_at)
    if created_at is None:
        return

    if (now - created_at).total_seconds() < MIN_OPEN_DELAY_SECONDS:
        return

    ip_address = get_client_ip(request) or (request.client.host if request.client else "")
    user_agent = request.headers.get("User-Agent", "")
    referer = request.headers.get("Referer", "")
    proxy_type = detect_proxy_type(ip_address, user_agent)
    country, city = lookup_ip(ip_address)

    email_recipient = tracked_email.recipient or "Unknown"
    email_subject = tracked_email.subject or "(no subject)"
    should_notify = (
        tracked_email.notified_at is None
        and is_email_notifications_enabled()
        and proxy_type is None
    )

    db.add(
        Open(
            tracked_email_id=tracking_id,
            ip_address=ip_address,
            user_agent=user_agent,
            referer=referer,
            country=country,
            city=city,
        )
    )

    if should_notify:
        tracked_email.notified_at = now

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

    if proxy_type is not None or not is_email_notifications_enabled():
        return

    await _maybe_queue_hot_conversation_notification(
        db,
        tracking_id,
        now,
        background_tasks,
        email_recipient,
        email_subject,
    )
    await _maybe_queue_revived_conversation_notification(
        db,
        tracking_id,
        now,
        background_tasks,
        email_recipient,
        email_subject,
    )


async def _maybe_queue_hot_conversation_notification(
    db: AsyncSession,
    tracking_id: str,
    now: datetime,
    background_tasks: BackgroundTasks,
    recipient: str,
    subject: str,
) -> None:
    result = await db.execute(
        select(TrackedEmail).where(TrackedEmail.id == tracking_id)
    )
    tracked_email = result.scalar_one_or_none()
    if tracked_email is None or tracked_email.hot_notified_at is not None:
        return

    twenty_four_hours_ago = now - timedelta(hours=24)
    count_result = await db.execute(
        select(func.count(Open.id))
        .where(Open.tracked_email_id == tracking_id)
        .where(Open.opened_at >= twenty_four_hours_ago)
    )
    open_count = count_result.scalar() or 0
    if open_count < 3:
        return

    tracked_email.hot_notified_at = now
    await db.commit()

    background_tasks.add_task(
        send_hot_conversation_notification,
        recipient=recipient,
        subject=subject,
        open_count=open_count,
        track_id=tracking_id,
    )


async def _maybe_queue_revived_conversation_notification(
    db: AsyncSession,
    tracking_id: str,
    now: datetime,
    background_tasks: BackgroundTasks,
    recipient: str,
    subject: str,
) -> None:
    result = await db.execute(
        select(TrackedEmail).where(TrackedEmail.id == tracking_id)
    )
    tracked_email = result.scalar_one_or_none()
    if tracked_email is None or tracked_email.revived_notified_at is not None:
        return

    first_open_result = await db.execute(
        select(func.min(Open.opened_at)).where(Open.tracked_email_id == tracking_id)
    )
    first_open_at = ensure_utc(first_open_result.scalar())
    if first_open_at is None:
        return

    days_since_first_open = (now - first_open_at).days
    if days_since_first_open < 14:
        return

    tracked_email.revived_notified_at = now
    await db.commit()

    background_tasks.add_task(
        send_revived_conversation_notification,
        recipient=recipient,
        subject=subject,
        days_since_first_open=days_since_first_open,
        track_id=tracking_id,
    )
