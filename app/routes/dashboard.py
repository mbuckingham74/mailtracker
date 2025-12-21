from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from starlette.middleware.sessions import SessionMiddleware
import uuid
import os
import ipaddress
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from ..database import get_db, TrackedEmail, Open

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")


def get_pixel_url(track_id: str) -> str:
    """Generate pixel URL for a track. Requires BASE_URL to be configured."""
    if not BASE_URL:
        return f"/p/{track_id}.gif"
    return f"{BASE_URL}/p/{track_id}.gif"


# EST timezone (UTC-5)
EST = timezone(timedelta(hours=-5))

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Apple Mail Privacy Protection and other proxy IP ranges
APPLE_IP_RANGES = [
    ipaddress.ip_network('17.0.0.0/8'),      # Apple's primary range
    ipaddress.ip_network('104.28.0.0/16'),   # Cloudflare (used by Apple)
]

GOOGLE_PROXY_RANGES = [
    ipaddress.ip_network('66.102.0.0/20'),   # Google
    ipaddress.ip_network('66.249.64.0/19'),  # Googlebot
    ipaddress.ip_network('72.14.192.0/18'),  # Google
    ipaddress.ip_network('74.125.0.0/16'),   # Google (includes image proxy)
    ipaddress.ip_network('209.85.128.0/17'), # Google
]


def detect_proxy_type(ip_str: str, user_agent: str = "") -> str | None:
    """Detect if an IP is from a known email proxy service."""
    if not ip_str:
        return None

    try:
        ip = ipaddress.ip_address(ip_str)

        # Check Apple ranges
        for network in APPLE_IP_RANGES:
            if ip in network:
                return "apple"

        # Check Google ranges
        for network in GOOGLE_PROXY_RANGES:
            if ip in network:
                return "google"

        # Also check user agent for proxy indicators
        if user_agent:
            ua_lower = user_agent.lower()
            if "googleimageproxy" in ua_lower or "ggpht.com" in ua_lower:
                return "google"
            if "apple" in ua_lower and "mail" in ua_lower:
                return "apple"

    except ValueError:
        pass

    return None


def to_est(dt):
    """Convert a datetime to EST timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EST)

# Make functions available in templates
templates.env.globals["detect_proxy_type"] = detect_proxy_type
templates.env.globals["to_est"] = to_est

DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")


def is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated", False)


def require_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return True


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == DASHBOARD_USERNAME and password == DASHBOARD_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(
        select(TrackedEmail).order_by(TrackedEmail.created_at.desc())
    )
    tracks = result.scalars().all()

    # Group tracks by message_group_id
    groups = {}  # group_id -> list of tracks
    ungrouped = []  # tracks without group_id

    for track in tracks:
        # Get all opens for this track
        opens_result = await db.execute(
            select(Open).where(Open.tracked_email_id == track.id).order_by(Open.opened_at.asc())
        )
        opens = opens_result.scalars().all()

        open_count = len(opens)
        # Separate proxy opens from real opens
        proxy_opens = []
        real_opens = []
        for o in opens:
            proxy_type = detect_proxy_type(o.ip_address, o.user_agent or '')
            if proxy_type:
                proxy_opens.append((o, proxy_type))
            else:
                real_opens.append(o)

        real_open_count = len(real_opens)

        # First open (any)
        first_open = opens[0].opened_at if opens else None
        # First real open (non-proxy)
        first_real_open = real_opens[0].opened_at if real_opens else None
        # First proxy open with type (for "Delivered to Gmail/iCloud" display)
        first_proxy_open = proxy_opens[0][0].opened_at if proxy_opens else None
        first_proxy_type = proxy_opens[0][1] if proxy_opens else None

        track_data = {
            "track": track,
            "open_count": open_count,
            "real_open_count": real_open_count,
            "first_open": first_open,
            "first_real_open": first_real_open,
            "first_proxy_open": first_proxy_open,
            "first_proxy_type": first_proxy_type
        }

        if track.message_group_id:
            if track.message_group_id not in groups:
                groups[track.message_group_id] = []
            groups[track.message_group_id].append(track_data)
        else:
            ungrouped.append(track_data)

    # Build final list: grouped tracks first (by earliest created_at), then ungrouped
    tracks_with_counts = []

    # Sort groups by earliest created_at
    sorted_groups = sorted(
        groups.items(),
        key=lambda x: min(t["track"].created_at for t in x[1]),
        reverse=True
    )

    for group_id, group_tracks in sorted_groups:
        # Sort recipients within group by created_at
        group_tracks.sort(key=lambda x: x["track"].created_at)
        # Get first proxy info from any recipient in the group
        proxy_tracks = [(t["first_proxy_open"], t["first_proxy_type"]) for t in group_tracks if t["first_proxy_open"]]
        first_proxy = min(proxy_tracks, key=lambda x: x[0]) if proxy_tracks else (None, None)
        tracks_with_counts.append({
            "is_group": True,
            "group_id": group_id,
            "subject": group_tracks[0]["track"].subject,
            "created_at": group_tracks[0]["track"].created_at,
            "recipients": group_tracks,
            "total_opens": sum(t["open_count"] for t in group_tracks),
            "total_real_opens": sum(t["real_open_count"] for t in group_tracks),
            "first_open": min((t["first_open"] for t in group_tracks if t["first_open"]), default=None),
            "first_real_open": min((t["first_real_open"] for t in group_tracks if t["first_real_open"]), default=None),
            "first_proxy_open": first_proxy[0],
            "first_proxy_type": first_proxy[1]
        })

    # Add ungrouped tracks
    for track_data in ungrouped:
        tracks_with_counts.append({
            "is_group": False,
            **track_data
        })

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tracks": tracks_with_counts
    })


@router.get("/create", response_class=HTMLResponse)
async def create_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("create.html", {"request": request})


@router.post("/create")
async def create_track(
    request: Request,
    db: AsyncSession = Depends(get_db),
    recipient: str = Form(""),
    subject: str = Form(""),
    notes: str = Form("")
):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    
    track_id = str(uuid.uuid4())
    
    new_track = TrackedEmail(
        id=track_id,
        recipient=recipient or None,
        subject=subject or None,
        notes=notes or None
    )
    
    db.add(new_track)
    await db.commit()
    
    return RedirectResponse(url=f"/detail/{track_id}", status_code=303)


@router.get("/detail/{track_id}", response_class=HTMLResponse)
async def detail_page(request: Request, track_id: str, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    
    result = await db.execute(
        select(TrackedEmail).where(TrackedEmail.id == track_id)
    )
    track = result.scalar_one_or_none()
    
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Get opens (ascending for first proxy detection)
    opens_result = await db.execute(
        select(Open).where(Open.tracked_email_id == track_id).order_by(Open.opened_at.asc())
    )
    opens_asc = opens_result.scalars().all()

    # Separate proxy and real opens
    proxy_opens = []
    real_opens = []
    for o in opens_asc:
        proxy_type = detect_proxy_type(o.ip_address, o.user_agent or '')
        if proxy_type:
            proxy_opens.append((o, proxy_type))
        else:
            real_opens.append(o)

    # First proxy open info
    first_proxy_open = proxy_opens[0][0].opened_at if proxy_opens else None
    first_proxy_type = proxy_opens[0][1] if proxy_opens else None

    # Reverse for display (most recent first)
    opens = list(reversed(opens_asc))

    pixel_url = get_pixel_url(track.id)
    html_snippet = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="" />'

    # Build Gmail search URL to find the original sent email
    gmail_search_parts = ["in:sent"]
    if track.recipient:
        # Handle comma-separated recipients - just use first one for search
        first_recipient = track.recipient.split(',')[0].strip()
        gmail_search_parts.append(f"to:{first_recipient}")
    if track.subject:
        gmail_search_parts.append(f"subject:{track.subject}")
    gmail_search_query = " ".join(gmail_search_parts)
    gmail_search_url = f"https://mail.google.com/mail/u/0/#search/{quote(gmail_search_query)}"

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "track": track,
        "opens": opens,
        "real_open_count": len(real_opens),
        "first_proxy_open": first_proxy_open,
        "first_proxy_type": first_proxy_type,
        "pixel_url": pixel_url,
        "html_snippet": html_snippet,
        "gmail_search_url": gmail_search_url
    })


@router.post("/delete/{track_id}")
async def delete_track(request: Request, track_id: str, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    
    await db.execute(delete(TrackedEmail).where(TrackedEmail.id == track_id))
    await db.commit()
    
    return RedirectResponse(url="/", status_code=303)
