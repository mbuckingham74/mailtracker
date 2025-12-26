from fastapi import APIRouter, Request, Depends, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from ..database import get_db, TrackedEmail, Open
from ..geoip import lookup_ip
from ..proxy_detection import detect_proxy_type
from ..notifications import send_open_notification, send_hot_conversation_notification, send_revived_conversation_notification, is_email_notifications_enabled
import base64
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

router = APIRouter()

# 1x1 transparent GIF (43 bytes)
PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

# Minimum seconds after track creation before opens are counted
# This filters out the sender's browser loading the pixel during send
# Set to 5 seconds - enough to filter instant browser loads but allow real opens
MIN_OPEN_DELAY_SECONDS = 5

@router.get("/p/{tracking_id}.gif")
async def track_pixel(
    tracking_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    # Always return pixel regardless of whether tracking_id exists
    # This prevents information leakage

    try:
        # Check if tracking ID exists
        result = await db.execute(
            select(TrackedEmail).where(TrackedEmail.id == tracking_id)
        )
        tracked_email = result.scalar_one_or_none()

        if tracked_email:
            # Filter out opens that happen too quickly after track creation
            # This is the sender's browser loading the pixel, not a recipient
            now = datetime.now(timezone.utc)
            # Handle naive datetime from DB
            created_at = tracked_email.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            seconds_since_creation = (now - created_at).total_seconds()

            if seconds_since_creation < MIN_OPEN_DELAY_SECONDS:
                # Too soon - this is likely the sender's browser, ignore it
                pass
            else:
                # Get client info
                ip_address = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For") or request.client.host
                # Handle comma-separated list of IPs (from proxies)
                if ip_address and "," in ip_address:
                    ip_address = ip_address.split(",")[0].strip()

                user_agent = request.headers.get("User-Agent", "")
                referer = request.headers.get("Referer", "")

                # GeoIP lookup
                country, city = lookup_ip(ip_address)

                # Capture values BEFORE any commits (SQLAlchemy expires objects after commit)
                email_recipient = tracked_email.recipient or "Unknown"
                email_subject = tracked_email.subject or "(no subject)"
                email_sent_at = created_at  # Already has timezone info from above
                should_notify = (
                    tracked_email.notified_at is None and
                    is_email_notifications_enabled() and
                    detect_proxy_type(ip_address, user_agent) is None  # Real open, not proxy
                )

                # Log the open
                open_record = Open(
                    tracked_email_id=tracking_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    referer=referer,
                    country=country,
                    city=city
                )
                db.add(open_record)

                # If this is a real open and we should notify, mark as notified first
                if should_notify:
                    tracked_email.notified_at = now

                await db.commit()

                # Send notification after commit (blocking SMTP call)
                if should_notify:
                    try:
                        send_open_notification(
                            recipient=email_recipient,
                            subject=email_subject,
                            opened_at=now,
                            country=country,
                            city=city,
                            track_id=tracking_id,
                            sent_at=email_sent_at
                        )
                    except Exception as notify_error:
                        logger.error(f"Failed to send notification for {tracking_id}: {notify_error}")

                # Check for hot conversation (3+ real opens in 24 hours)
                # Only check if this was a real open (not proxy) and we haven't already notified
                is_real_open = detect_proxy_type(ip_address, user_agent) is None
                if is_real_open and is_email_notifications_enabled():
                    # Re-fetch tracked_email to check hot_notified_at
                    result = await db.execute(
                        select(TrackedEmail).where(TrackedEmail.id == tracking_id)
                    )
                    tracked_email_fresh = result.scalar_one_or_none()

                    if tracked_email_fresh and tracked_email_fresh.hot_notified_at is None:
                        # Count real opens in last 24 hours (excluding proxy opens is hard,
                        # so we count all opens - proxy opens are relatively rare)
                        twenty_four_hours_ago = now - timedelta(hours=24)
                        count_result = await db.execute(
                            select(func.count(Open.id))
                            .where(Open.tracked_email_id == tracking_id)
                            .where(Open.opened_at >= twenty_four_hours_ago)
                        )
                        open_count = count_result.scalar() or 0

                        if open_count >= 3:
                            # Mark as notified first
                            tracked_email_fresh.hot_notified_at = now
                            await db.commit()

                            try:
                                send_hot_conversation_notification(
                                    recipient=email_recipient,
                                    subject=email_subject,
                                    open_count=open_count,
                                    track_id=tracking_id
                                )
                            except Exception as hot_error:
                                logger.error(f"Failed to send hot conversation notification for {tracking_id}: {hot_error}")

                    # Check for revived conversation (open 2+ weeks after first open)
                    # Re-fetch to get current state after potential hot notification update
                    result = await db.execute(
                        select(TrackedEmail).where(TrackedEmail.id == tracking_id)
                    )
                    tracked_email_fresh = result.scalar_one_or_none()

                    if tracked_email_fresh and tracked_email_fresh.revived_notified_at is None:
                        # Get the first open timestamp
                        first_open_result = await db.execute(
                            select(func.min(Open.opened_at))
                            .where(Open.tracked_email_id == tracking_id)
                        )
                        first_open_at = first_open_result.scalar()

                        if first_open_at:
                            # Handle naive datetime
                            if first_open_at.tzinfo is None:
                                first_open_at = first_open_at.replace(tzinfo=timezone.utc)

                            days_since_first_open = (now - first_open_at).days

                            if days_since_first_open >= 14:
                                # Mark as notified first
                                tracked_email_fresh.revived_notified_at = now
                                await db.commit()

                                try:
                                    send_revived_conversation_notification(
                                        recipient=email_recipient,
                                        subject=email_subject,
                                        days_since_first_open=days_since_first_open,
                                        track_id=tracking_id
                                    )
                                except Exception as revived_error:
                                    logger.error(f"Failed to send revived conversation notification for {tracking_id}: {revived_error}")
    except Exception as e:
        # Log the error but don't break pixel delivery
        logger.exception(f"Failed to record open for tracking_id={tracking_id}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass  # Rollback may fail if session is in a bad state

    return Response(
        content=PIXEL_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
