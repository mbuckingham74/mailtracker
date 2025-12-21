from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from starlette.middleware.sessions import SessionMiddleware
import uuid
import os

from ..database import get_db, TrackedEmail, Open

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

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
        count_result = await db.execute(
            select(func.count(Open.id)).where(Open.tracked_email_id == track.id)
        )
        open_count = count_result.scalar() or 0

        first_open_result = await db.execute(
            select(Open.opened_at).where(Open.tracked_email_id == track.id).order_by(Open.opened_at.asc()).limit(1)
        )
        first_open = first_open_result.scalar_one_or_none()

        track_data = {
            "track": track,
            "open_count": open_count,
            "first_open": first_open
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
        tracks_with_counts.append({
            "is_group": True,
            "group_id": group_id,
            "subject": group_tracks[0]["track"].subject,
            "created_at": group_tracks[0]["track"].created_at,
            "recipients": group_tracks,
            "total_opens": sum(t["open_count"] for t in group_tracks),
            "first_open": min((t["first_open"] for t in group_tracks if t["first_open"]), default=None)
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
    
    # Get opens
    opens_result = await db.execute(
        select(Open).where(Open.tracked_email_id == track_id).order_by(Open.opened_at.desc())
    )
    opens = opens_result.scalars().all()
    
    pixel_url = f"https://mailtrack.tachyonfuture.com/p/{track.id}.gif"
    html_snippet = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="" />'
    
    return templates.TemplateResponse("detail.html", {
        "request": request,
        "track": track,
        "opens": opens,
        "pixel_url": pixel_url,
        "html_snippet": html_snippet
    })


@router.post("/delete/{track_id}")
async def delete_track(request: Request, track_id: str, db: AsyncSession = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    
    await db.execute(delete(TrackedEmail).where(TrackedEmail.id == track_id))
    await db.commit()
    
    return RedirectResponse(url="/", status_code=303)
