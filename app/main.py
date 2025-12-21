from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

from .routes import pixel, api, dashboard
from .geoip import init_geoip


def _require_env(name: str) -> str:
    """Get required environment variable or raise error at startup."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize GeoIP database
    await init_geoip()
    yield
    # Shutdown: nothing to clean up


app = FastAPI(title="Mailtrack", docs_url=None, redoc_url=None, lifespan=lifespan)

# Session middleware for dashboard auth with secure cookie flags
SECRET_KEY = _require_env("SECRET_KEY")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=os.getenv("COOKIE_SECURE", "true").lower() == "true",
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
    return {"status": "ok"}
