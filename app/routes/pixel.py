from fastapi import APIRouter, Request, Depends, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db, TrackedEmail, Open
from ..geoip import lookup_ip
from ..proxy_detection import detect_proxy_type
from ..notifications import send_open_notification, is_email_notifications_enabled
import base64
import logging
from datetime import datetime, timezone

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
                await db.commit()

                # Check if this is a real open (not proxy) and send notification
                proxy_type = detect_proxy_type(ip_address, user_agent)
                if proxy_type is None and tracked_email.notified_at is None:
                    # This is the first real open - send notification
                    if is_email_notifications_enabled():
                        # Mark as notified FIRST to prevent race conditions
                        # (do this before the blocking SMTP call)
                        tracked_email.notified_at = datetime.now(timezone.utc)
                        await db.commit()

                        try:
                            send_open_notification(
                                recipient=tracked_email.recipient or "Unknown",
                                subject=tracked_email.subject or "(no subject)",
                                opened_at=open_record.opened_at,
                                country=country,
                                city=city,
                                track_id=tracking_id
                            )
                        except Exception as notify_error:
                            logger.error(f"Failed to send notification for {tracking_id}: {notify_error}")
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
