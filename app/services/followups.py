import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from ..config import settings
from ..database import TrackedEmail, async_session
from ..notifications import is_email_notifications_enabled, send_followup_reminder
from ..time_utils import ensure_utc
from .open_activity import load_real_open_summaries

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FollowupTrackSnapshot:
    id: str
    recipient: str | None
    subject: str | None
    created_at: datetime | None


async def check_followup_reminders() -> None:
    """Check for unopened emails and send follow-up reminders."""
    if not is_email_notifications_enabled():
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.followup_days)

    async with async_session() as db:
        result = await db.execute(
            select(
                TrackedEmail.id,
                TrackedEmail.recipient,
                TrackedEmail.subject,
                TrackedEmail.created_at,
            ).where(
                TrackedEmail.created_at <= cutoff,
                TrackedEmail.followup_notified_at.is_(None),
            )
        )
        tracks = [
            FollowupTrackSnapshot(
                id=track_id,
                recipient=recipient,
                subject=subject,
                created_at=created_at,
            )
            for track_id, recipient, subject, created_at in result
        ]
        if not tracks:
            return

        real_open_track_ids = set(
            await load_real_open_summaries(db, track_ids=[track.id for track in tracks])
        )

        already_opened_track_ids = [track.id for track in tracks if track.id in real_open_track_ids]
        if already_opened_track_ids:
            await db.execute(
                update(TrackedEmail)
                .where(TrackedEmail.id.in_(already_opened_track_ids))
                .values(followup_notified_at=now)
            )
            await db.commit()

        for track in tracks:
            if track.id in real_open_track_ids:
                continue

            created_at = ensure_utc(track.created_at)
            if created_at is None:
                continue

            days_ago = (now - created_at).days
            success = await asyncio.to_thread(
                send_followup_reminder,
                recipient=track.recipient,
                subject=track.subject,
                sent_at=created_at,
                days_ago=days_ago,
                track_id=track.id,
            )
            if success:
                await db.execute(
                    update(TrackedEmail)
                    .where(TrackedEmail.id == track.id)
                    .values(followup_notified_at=now)
                )
                await db.commit()
                logger.info("Follow-up reminder sent for track %s", track.id)
