from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .database import check_database_health, init_database
from .geoip import init_geoip
from .paths import STATIC_DIR
from .routes import api, dashboard, pixel
from .services.followups import check_followup_reminders

logger = logging.getLogger(__name__)


async def followup_reminder_task():
    """Background task that checks for follow-up reminders every hour."""
    while True:
        try:
            await check_followup_reminders()
        except Exception:
            logger.exception("Error in follow-up reminder task")
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
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
