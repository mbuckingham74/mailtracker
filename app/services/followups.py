import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..config import settings
from ..database import TrackedEmail, async_session
from ..notifications import is_email_notifications_enabled, send_followup_reminder
from ..time_utils import ensure_utc
from .open_activity import load_real_open_summaries

logger = logging.getLogger(__name__)


async def check_followup_reminders() -> None:
    """Check for unopened emails and send follow-up reminders."""
    if not is_email_notifications_enabled():
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.followup_days)

    async with async_session() as db:
        result = await db.execute(
            select(TrackedEmail).where(
                TrackedEmail.created_at <= cutoff,
                TrackedEmail.followup_notified_at.is_(None),
            )
        )
        tracks = result.scalars().all()
        if not tracks:
            return

        real_open_track_ids = set(
            await load_real_open_summaries(db, track_ids=[track.id for track in tracks])
        )

        for track in tracks:
            if track.id in real_open_track_ids:
                track.followup_notified_at = now
                await db.commit()
                continue

            created_at = ensure_utc(track.created_at)
            if created_at is None:
                continue

            days_ago = (now - created_at).days
            success = await asyncio.to_thread(
                send_followup_reminder,
                recipient=track.recipient,
                subject=track.subject,
                sent_at=track.created_at,
                days_ago=days_ago,
                track_id=track.id,
            )
            if success:
                track.followup_notified_at = now
                await db.commit()
                logger.info("Follow-up reminder sent for track %s", track.id)
