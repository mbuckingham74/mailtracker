from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .routes import pixel, api, dashboard
from .geoip import init_geoip
from .database import async_session, TrackedEmail, Open, check_database_health, init_database
from .notifications import send_followup_reminder, is_email_notifications_enabled
from .proxy_detection import detect_proxy_type
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def check_followup_reminders():
    """Check for unopened emails and send follow-up reminders."""
    if not is_email_notifications_enabled():
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.followup_days)

    async with async_session() as db:
        # Find emails older than FOLLOWUP_DAYS that haven't had a follow-up reminder
        result = await db.execute(
            select(TrackedEmail).where(
                TrackedEmail.created_at <= cutoff,
                TrackedEmail.followup_notified_at.is_(None)
            )
        )
        tracks = result.scalars().all()

        for track in tracks:
            # Check if this email has any real opens
            opens_result = await db.execute(
                select(Open).where(Open.tracked_email_id == track.id)
            )
            opens = opens_result.scalars().all()

            # Filter for real opens (exclude proxy)
            has_real_open = False
            for o in opens:
                proxy_type = detect_proxy_type(o.ip_address, o.user_agent or '')
                if not proxy_type:
                    has_real_open = True
                    break

            # If no real opens, send follow-up reminder
            if not has_real_open:
                days_ago = (now - track.created_at.replace(tzinfo=timezone.utc)).days
                success = await asyncio.to_thread(
                    send_followup_reminder,
                    recipient=track.recipient,
                    subject=track.subject,
                    sent_at=track.created_at,
                    days_ago=days_ago,
                    track_id=track.id
                )
                if success:
                    track.followup_notified_at = now
                    await db.commit()
                    logger.info(f"Follow-up reminder sent for track {track.id}")
            else:
                # Email was opened, mark as notified to avoid checking again
                track.followup_notified_at = now
                await db.commit()


async def followup_reminder_task():
    """Background task that checks for follow-up reminders every hour."""
    while True:
        try:
            await check_followup_reminders()
        except Exception as e:
            logger.error(f"Error in follow-up reminder task: {e}")
        # Check every hour
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()

    # Startup: Initialize GeoIP database
    await init_geoip()

    # Start background task for follow-up reminders
    task = asyncio.create_task(followup_reminder_task())
    logger.info("Follow-up reminder background task started")

    yield

    # Shutdown: cancel background task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Mailtrack", docs_url=None, redoc_url=None, lifespan=lifespan)

# Session middleware for dashboard auth with secure cookie flags
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.cookie_secure,
    same_site="lax",
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(pixel.router)  # Pixel tracking (public)
app.include_router(api.router)    # REST API (API key protected)
app.include_router(dashboard.router)  # Web UI (session protected)


@app.get("/health")
async def health_check():
    database_ok, _ = await check_database_health()
    if not database_ok:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unavailable"},
        )

    return {"status": "ok", "database": "ok"}
