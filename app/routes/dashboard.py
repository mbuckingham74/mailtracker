from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, or_
from starlette.middleware.sessions import SessionMiddleware
import uuid
import os
import io
import csv
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import quote, urlencode
from collections import defaultdict
from statistics import median

from ..database import get_db, TrackedEmail, Open
from ..proxy_detection import detect_proxy_type

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("Required environment variable BASE_URL is not set (e.g., https://mailtrack.example.com)")


def get_pixel_url(track_id: str) -> str:
    """Generate absolute pixel URL for a track."""
    return f"{BASE_URL}/p/{track_id}.gif"


# Configurable display timezone (defaults to America/New_York for EST/EDT with DST)
_tz_name = os.getenv("DISPLAY_TIMEZONE", "America/New_York")
try:
    DISPLAY_TIMEZONE = ZoneInfo(_tz_name)
except ZoneInfoNotFoundError:
    raise RuntimeError(
        f"Invalid DISPLAY_TIMEZONE '{_tz_name}'. "
        f"Use an IANA timezone name like 'America/New_York' or 'Europe/London'."
    )

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def to_local(dt):
    """Convert a datetime to the configured display timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume UTC if naive (MySQL stores in UTC)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TIMEZONE)

# Make functions available in templates
templates.env.globals["detect_proxy_type"] = detect_proxy_type
templates.env.globals["to_local"] = to_local

DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
if not DASHBOARD_USERNAME or not DASHBOARD_PASSWORD:
    raise RuntimeError("Required environment variables DASHBOARD_USERNAME and DASHBOARD_PASSWORD are not set")


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


# Pagination settings
ITEMS_PER_PAGE = 25


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    filter: str = "all",
    search: str = "",
    date_range: str = "all",
    page: int = 1,
    db: AsyncSession = Depends(get_db)
):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    # Validate filter parameter
    if filter not in ("all", "opened", "unopened"):
        filter = "all"

    # Validate date_range parameter
    if date_range not in ("all", "7", "30", "90"):
        date_range = "all"

    # Validate page
    if page < 1:
        page = 1

    # Build query with search and date filters
    query = select(TrackedEmail).order_by(TrackedEmail.created_at.desc())

    # Apply search filter
    search = search.strip()
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                TrackedEmail.recipient.ilike(search_pattern),
                TrackedEmail.subject.ilike(search_pattern)
            )
        )

    # Apply date range filter
    if date_range != "all":
        days = int(date_range)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(TrackedEmail.created_at >= cutoff)

    result = await db.execute(query)
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

        # Apply filter (based on real opens, excluding proxy)
        if filter == "opened" and real_open_count == 0:
            continue
        if filter == "unopened" and real_open_count > 0:
            continue

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

    # Pagination
    total_items = len(tracks_with_counts)
    total_pages = max(1, (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    if page > total_pages:
        page = total_pages
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    paginated_tracks = tracks_with_counts[start_idx:end_idx]

    # Build query string for pagination links (preserve other filters)
    query_params = {}
    if filter != "all":
        query_params["filter"] = filter
    if search:
        query_params["search"] = search
    if date_range != "all":
        query_params["date_range"] = date_range

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tracks": paginated_tracks,
        "filter": filter,
        "search": search,
        "date_range": date_range,
        "page": page,
        "total_pages": total_pages,
        "total_items": total_items,
        "query_params": query_params
    })


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


@router.get("/export")
async def export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    """Export all tracking data as CSV with one row per open event."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    # Query all tracked emails with their opens
    result = await db.execute(
        select(TrackedEmail).order_by(TrackedEmail.created_at.desc())
    )
    tracks = result.scalars().all()

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        "email_id",
        "recipient",
        "subject",
        "email_created_at",
        "opened_at",
        "ip_address",
        "country",
        "city",
        "user_agent",
        "proxy_type",
        "is_real_open"
    ])

    # Data rows - one per open event
    for track in tracks:
        opens_result = await db.execute(
            select(Open).where(Open.tracked_email_id == track.id).order_by(Open.opened_at.asc())
        )
        opens = opens_result.scalars().all()

        email_created = to_local(track.created_at).strftime('%Y-%m-%d %H:%M:%S %Z') if track.created_at else ""

        for open_event in opens:
            proxy_type = detect_proxy_type(open_event.ip_address, open_event.user_agent or '')
            opened_at = to_local(open_event.opened_at).strftime('%Y-%m-%d %H:%M:%S %Z') if open_event.opened_at else ""

            writer.writerow([
                track.id,
                track.recipient or "",
                track.subject or "",
                email_created,
                opened_at,
                open_event.ip_address or "",
                open_event.country or "",
                open_event.city or "",
                open_event.user_agent or "",
                proxy_type or "",
                "no" if proxy_type else "yes"
            ])

    # Prepare response
    output.seek(0)
    export_date = datetime.now(DISPLAY_TIMEZONE).strftime('%Y-%m-%d')
    filename = f"mailtrack_export_{export_date}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics(
    request: Request,
    date_range: str = "30",
    db: AsyncSession = Depends(get_db)
):
    """Analytics dashboard with charts and statistics."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    # Validate date_range parameter
    if date_range not in ("7", "30", "90", "all"):
        date_range = "30"

    # Calculate date cutoff
    now = datetime.now(timezone.utc)
    if date_range == "all":
        cutoff = None
    else:
        days = int(date_range)
        cutoff = now - timedelta(days=days)

    # Query tracked emails
    query = select(TrackedEmail)
    if cutoff:
        query = query.where(TrackedEmail.created_at >= cutoff)
    result = await db.execute(query)
    tracks = result.scalars().all()

    # Query all opens
    opens_query = select(Open)
    if cutoff:
        opens_query = opens_query.where(Open.opened_at >= cutoff)
    opens_result = await db.execute(opens_query)
    all_opens = opens_result.scalars().all()

    # Separate real opens from proxy opens
    real_opens = []
    for o in all_opens:
        proxy_type = detect_proxy_type(o.ip_address, o.user_agent or '')
        if not proxy_type:
            real_opens.append(o)

    # Calculate summary statistics
    total_emails = len(tracks)
    total_real_opens = len(real_opens)

    # Open rate: % of emails with at least one real open
    emails_with_opens = set()
    for o in real_opens:
        emails_with_opens.add(o.tracked_email_id)
    open_rate = (len(emails_with_opens) / total_emails * 100) if total_emails > 0 else 0

    # Calculate time to first open for each email
    # First, get first real open time for each email
    first_real_open_times = {}
    for o in real_opens:
        if o.tracked_email_id not in first_real_open_times:
            first_real_open_times[o.tracked_email_id] = o.opened_at
        elif o.opened_at < first_real_open_times[o.tracked_email_id]:
            first_real_open_times[o.tracked_email_id] = o.opened_at

    # Calculate time deltas
    time_to_open_hours = []
    track_created_map = {t.id: t.created_at for t in tracks}
    for email_id, first_open in first_real_open_times.items():
        if email_id in track_created_map:
            created = track_created_map[email_id]
            if created and first_open:
                # Make both timezone-aware for comparison
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if first_open.tzinfo is None:
                    first_open = first_open.replace(tzinfo=timezone.utc)
                delta = (first_open - created).total_seconds() / 3600  # hours
                if delta > 0:
                    time_to_open_hours.append(delta)

    avg_time_to_open = median(time_to_open_hours) if time_to_open_hours else None

    # Format avg time to open for display
    if avg_time_to_open is not None:
        if avg_time_to_open < 1:
            avg_time_to_open_display = f"{int(avg_time_to_open * 60)} min"
        elif avg_time_to_open < 24:
            avg_time_to_open_display = f"{avg_time_to_open:.1f} hrs"
        else:
            avg_time_to_open_display = f"{avg_time_to_open / 24:.1f} days"
    else:
        avg_time_to_open_display = "N/A"

    # Determine chart granularity based on date range
    if date_range in ("7", "30"):
        granularity = "daily"
    elif date_range == "90":
        granularity = "weekly"
    else:
        granularity = "monthly"

    # Time series data: emails sent and opens over time
    emails_by_date = defaultdict(int)
    opens_by_date = defaultdict(int)

    for track in tracks:
        if track.created_at:
            date_key = _get_date_key(track.created_at, granularity)
            emails_by_date[date_key] += 1

    for o in real_opens:
        if o.opened_at:
            date_key = _get_date_key(o.opened_at, granularity)
            opens_by_date[date_key] += 1

    # Generate all date keys for the range
    all_date_keys = _generate_date_keys(cutoff or datetime(2020, 1, 1, tzinfo=timezone.utc), now, granularity)

    time_series_labels = all_date_keys
    time_series_emails = [emails_by_date.get(k, 0) for k in all_date_keys]
    time_series_opens = [opens_by_date.get(k, 0) for k in all_date_keys]

    # Geographic data: opens by country
    opens_by_country = defaultdict(int)
    opens_by_city = defaultdict(int)
    for o in real_opens:
        country = o.country or "Unknown"
        opens_by_country[country] += 1
        if o.city:
            opens_by_city[f"{o.city}, {country}"] += 1

    top_countries = sorted(opens_by_country.items(), key=lambda x: x[1], reverse=True)[:10]
    top_cities = sorted(opens_by_city.items(), key=lambda x: x[1], reverse=True)[:10]

    # Hour of day distribution (in local timezone)
    opens_by_hour = defaultdict(int)
    for o in real_opens:
        if o.opened_at:
            local_time = to_local(o.opened_at)
            opens_by_hour[local_time.hour] += 1

    hour_labels = [f"{h:02d}:00" for h in range(24)]
    hour_data = [opens_by_hour.get(h, 0) for h in range(24)]

    # Day of week distribution (in local timezone)
    opens_by_dow = defaultdict(int)
    dow_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for o in real_opens:
        if o.opened_at:
            local_time = to_local(o.opened_at)
            opens_by_dow[local_time.weekday()] += 1

    dow_data = [opens_by_dow.get(i, 0) for i in range(7)]

    # Time to first open distribution
    time_buckets = {
        "<1 hr": 0,
        "1-6 hrs": 0,
        "6-24 hrs": 0,
        "1-3 days": 0,
        "3-7 days": 0,
        ">7 days": 0
    }
    for hours in time_to_open_hours:
        if hours < 1:
            time_buckets["<1 hr"] += 1
        elif hours < 6:
            time_buckets["1-6 hrs"] += 1
        elif hours < 24:
            time_buckets["6-24 hrs"] += 1
        elif hours < 72:
            time_buckets["1-3 days"] += 1
        elif hours < 168:
            time_buckets["3-7 days"] += 1
        else:
            time_buckets[">7 days"] += 1

    time_bucket_labels = list(time_buckets.keys())
    time_bucket_data = list(time_buckets.values())

    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "date_range": date_range,
        # Summary stats
        "total_emails": total_emails,
        "total_real_opens": total_real_opens,
        "open_rate": round(open_rate, 1),
        "avg_time_to_open": avg_time_to_open_display,
        # Time series (as JSON for Chart.js)
        "time_series_labels": json.dumps(time_series_labels),
        "time_series_emails": json.dumps(time_series_emails),
        "time_series_opens": json.dumps(time_series_opens),
        # Geographic
        "country_labels": json.dumps([c[0] for c in top_countries]),
        "country_data": json.dumps([c[1] for c in top_countries]),
        "top_cities": top_cities,
        # Hour of day
        "hour_labels": json.dumps(hour_labels),
        "hour_data": json.dumps(hour_data),
        # Day of week
        "dow_labels": json.dumps(dow_names),
        "dow_data": json.dumps(dow_data),
        # Time to first open
        "time_bucket_labels": json.dumps(time_bucket_labels),
        "time_bucket_data": json.dumps(time_bucket_data),
    })


def _get_date_key(dt: datetime, granularity: str) -> str:
    """Convert datetime to a date key based on granularity."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(DISPLAY_TIMEZONE)

    if granularity == "daily":
        return local_dt.strftime("%Y-%m-%d")
    elif granularity == "weekly":
        # Get start of week (Monday)
        start_of_week = local_dt - timedelta(days=local_dt.weekday())
        return start_of_week.strftime("%Y-%m-%d")
    else:  # monthly
        return local_dt.strftime("%Y-%m")


def _generate_date_keys(start: datetime, end: datetime, granularity: str) -> list:
    """Generate all date keys between start and end."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    start_local = start.astimezone(DISPLAY_TIMEZONE)
    end_local = end.astimezone(DISPLAY_TIMEZONE)

    keys = []
    current = start_local

    if granularity == "daily":
        while current <= end_local:
            keys.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
    elif granularity == "weekly":
        # Start from Monday of the start week
        current = current - timedelta(days=current.weekday())
        while current <= end_local:
            keys.append(current.strftime("%Y-%m-%d"))
            current += timedelta(weeks=1)
    else:  # monthly
        while current <= end_local:
            keys.append(current.strftime("%Y-%m"))
            # Move to first of next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)

    return keys


def _calculate_engagement_score(sent: int, opened: int, last_open: datetime, now: datetime) -> int:
    """Calculate engagement score (0-100) for a recipient."""
    if sent == 0:
        return 0

    # Open rate component (0-50)
    open_rate = opened / sent
    open_rate_score = open_rate * 50

    # Recency component (0-25)
    # Full points if opened in last 7 days, decays over 90 days
    if last_open:
        if last_open.tzinfo is None:
            last_open = last_open.replace(tzinfo=timezone.utc)
        days_ago = (now - last_open).days
        if days_ago <= 7:
            recency_score = 25
        elif days_ago <= 90:
            recency_score = 25 * (1 - (days_ago - 7) / 83)
        else:
            recency_score = 0
    else:
        recency_score = 0

    # Consistency component (0-25)
    # Based on how many different emails they've opened
    if sent >= 3:
        consistency_score = open_rate * 25
    else:
        # Not enough data, give partial credit
        consistency_score = open_rate * 15

    return round(open_rate_score + recency_score + consistency_score)


def _get_engagement_label(score: int) -> str:
    """Get human-readable label for engagement score."""
    if score >= 80:
        return "Highly Engaged"
    elif score >= 60:
        return "Engaged"
    elif score >= 40:
        return "Moderate"
    elif score >= 20:
        return "Low"
    else:
        return "Unengaged"


def _format_time_ago(dt: datetime, now: datetime) -> str:
    """Format a datetime as relative time (e.g., '2 hours ago')."""
    if dt is None:
        return "Never"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    delta = now - dt
    seconds = delta.total_seconds()

    if seconds < 60:
        return "Just now"
    elif seconds < 3600:
        mins = int(seconds / 60)
        return f"{mins} min ago" if mins == 1 else f"{mins} mins ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hr ago" if hours == 1 else f"{hours} hrs ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day ago" if days == 1 else f"{days} days ago"
    elif seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks} week ago" if weeks == 1 else f"{weeks} weeks ago"
    else:
        return to_local(dt).strftime("%b %d, %Y")


@router.get("/recipients", response_class=HTMLResponse)
async def recipients_list(
    request: Request,
    search: str = "",
    sort: str = "score",
    order: str = "desc",
    page: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """List all recipients with engagement metrics."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    now = datetime.now(timezone.utc)

    # Get all tracked emails
    result = await db.execute(select(TrackedEmail))
    tracks = result.scalars().all()

    # Get all opens
    opens_result = await db.execute(select(Open))
    all_opens = opens_result.scalars().all()

    # Build a map of track_id -> list of real opens
    track_opens = defaultdict(list)
    for o in all_opens:
        proxy_type = detect_proxy_type(o.ip_address, o.user_agent or '')
        if not proxy_type:
            track_opens[o.tracked_email_id].append(o)

    # Aggregate by recipient email
    recipients = {}
    for track in tracks:
        if not track.recipient:
            continue

        # Handle comma-separated recipients
        recipient_emails = [e.strip().lower() for e in track.recipient.split(',')]

        for email in recipient_emails:
            if not email:
                continue

            if email not in recipients:
                recipients[email] = {
                    "email": email,
                    "display_email": email,  # Keep original case for display
                    "sent": 0,
                    "opened": 0,
                    "last_open": None,
                    "track_ids": []
                }

            # Use original email for display if we haven't seen it yet
            if recipients[email]["sent"] == 0:
                # Find original case from track
                for e in track.recipient.split(','):
                    if e.strip().lower() == email:
                        recipients[email]["display_email"] = e.strip()
                        break

            recipients[email]["sent"] += 1
            recipients[email]["track_ids"].append(track.id)

            # Check if this track was opened
            real_opens = track_opens.get(track.id, [])
            if real_opens:
                recipients[email]["opened"] += 1
                # Find earliest open
                first_open = min(o.opened_at for o in real_opens)
                if first_open:
                    if first_open.tzinfo is None:
                        first_open = first_open.replace(tzinfo=timezone.utc)
                    if recipients[email]["last_open"] is None or first_open > recipients[email]["last_open"]:
                        recipients[email]["last_open"] = first_open

    # Calculate scores and rates
    recipient_list = []
    for email, data in recipients.items():
        open_rate = (data["opened"] / data["sent"] * 100) if data["sent"] > 0 else 0
        score = _calculate_engagement_score(data["sent"], data["opened"], data["last_open"], now)

        recipient_list.append({
            "email": data["display_email"],
            "email_lower": email,
            "sent": data["sent"],
            "opened": data["opened"],
            "open_rate": round(open_rate, 1),
            "last_open": data["last_open"],
            "last_open_display": _format_time_ago(data["last_open"], now),
            "score": score,
            "score_label": _get_engagement_label(score)
        })

    # Apply search filter
    search = search.strip().lower()
    if search:
        recipient_list = [r for r in recipient_list if search in r["email_lower"]]

    # Sort
    sort_key = {
        "email": lambda x: x["email_lower"],
        "sent": lambda x: x["sent"],
        "opened": lambda x: x["opened"],
        "rate": lambda x: x["open_rate"],
        "last_open": lambda x: x["last_open"] or datetime.min.replace(tzinfo=timezone.utc),
        "score": lambda x: x["score"]
    }.get(sort, lambda x: x["score"])

    reverse = order == "desc"
    recipient_list.sort(key=sort_key, reverse=reverse)

    # Pagination
    total_items = len(recipient_list)
    total_pages = max(1, (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    paginated_recipients = recipient_list[start_idx:end_idx]

    return templates.TemplateResponse("recipients.html", {
        "request": request,
        "recipients": paginated_recipients,
        "search": search,
        "sort": sort,
        "order": order,
        "page": page,
        "total_pages": total_pages,
        "total_items": total_items
    })


@router.get("/recipients/{email:path}", response_class=HTMLResponse)
async def recipient_detail(
    request: Request,
    email: str,
    db: AsyncSession = Depends(get_db)
):
    """Show detailed engagement history for a single recipient."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    now = datetime.now(timezone.utc)
    email_lower = email.lower()

    # Get all tracked emails for this recipient
    result = await db.execute(
        select(TrackedEmail).order_by(TrackedEmail.created_at.desc())
    )
    all_tracks = result.scalars().all()

    # Filter to tracks for this recipient
    tracks = []
    display_email = email
    for track in all_tracks:
        if not track.recipient:
            continue
        recipient_emails = [e.strip().lower() for e in track.recipient.split(',')]
        if email_lower in recipient_emails:
            tracks.append(track)
            # Get display email from first match
            if display_email == email:
                for e in track.recipient.split(','):
                    if e.strip().lower() == email_lower:
                        display_email = e.strip()
                        break

    if not tracks:
        raise HTTPException(status_code=404, detail="Recipient not found")

    # Get opens for all tracks
    track_ids = [t.id for t in tracks]
    opens_result = await db.execute(
        select(Open).where(Open.tracked_email_id.in_(track_ids))
    )
    all_opens = opens_result.scalars().all()

    # Build map of track_id -> real opens
    track_opens = defaultdict(list)
    for o in all_opens:
        proxy_type = detect_proxy_type(o.ip_address, o.user_agent or '')
        if not proxy_type:
            track_opens[o.tracked_email_id].append(o)

    # Calculate stats
    sent = len(tracks)
    opened = 0
    last_open = None
    time_to_open_hours = []

    email_history = []
    for track in tracks:
        real_opens = track_opens.get(track.id, [])
        was_opened = len(real_opens) > 0
        first_open = None

        if was_opened:
            opened += 1
            first_open = min(o.opened_at for o in real_opens)
            if first_open:
                if first_open.tzinfo is None:
                    first_open = first_open.replace(tzinfo=timezone.utc)
                if last_open is None or first_open > last_open:
                    last_open = first_open

                # Calculate time to open
                created = track.created_at
                if created:
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    delta_hours = (first_open - created).total_seconds() / 3600
                    if delta_hours > 0:
                        time_to_open_hours.append(delta_hours)

        email_history.append({
            "track": track,
            "was_opened": was_opened,
            "first_open": first_open,
            "first_open_display": _format_time_ago(first_open, now) if first_open else None,
            "open_count": len(real_opens)
        })

    open_rate = (opened / sent * 100) if sent > 0 else 0
    score = _calculate_engagement_score(sent, opened, last_open, now)

    # Average time to open
    if time_to_open_hours:
        avg_time = median(time_to_open_hours)
        if avg_time < 1:
            avg_time_display = f"{int(avg_time * 60)} min"
        elif avg_time < 24:
            avg_time_display = f"{avg_time:.1f} hrs"
        else:
            avg_time_display = f"{avg_time / 24:.1f} days"
    else:
        avg_time_display = "N/A"

    return templates.TemplateResponse("recipient_detail.html", {
        "request": request,
        "email": display_email,
        "sent": sent,
        "opened": opened,
        "open_rate": round(open_rate, 1),
        "avg_time_to_open": avg_time_display,
        "score": score,
        "score_label": _get_engagement_label(score),
        "last_open": last_open,
        "last_open_display": _format_time_ago(last_open, now),
        "email_history": email_history
    })
