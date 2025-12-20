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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize GeoIP database
    await init_geoip()
    yield
    # Shutdown: nothing to clean up


app = FastAPI(title="Mailtrack", docs_url=None, redoc_url=None, lifespan=lifespan)

# Session middleware for dashboard auth
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-random-string")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(pixel.router)  # Pixel tracking (public)
app.include_router(api.router)    # REST API (API key protected)
app.include_router(dashboard.router)  # Web UI (session protected)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
