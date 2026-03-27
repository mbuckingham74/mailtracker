from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import uuid

from ..config import settings
from ..database import get_db, TrackedEmail, Open
from ..proxy_detection import detect_proxy_type
from ..urls import get_pixel_url

router = APIRouter(prefix="/api")

RECENT_REAL_OPENS_LIMIT = 50
RECENT_OPEN_BATCH_SIZE = 200

# Auth dependency
async def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


# Pydantic models
class TrackCreate(BaseModel):
    recipient: Optional[str] = None
    subject: Optional[str] = None
    notes: Optional[str] = None
    message_group_id: Optional[str] = None  # Groups multiple recipients from same email


class OpenResponse(BaseModel):
    id: int
    opened_at: datetime
    ip_address: Optional[str]
    user_agent: Optional[str]
    referer: Optional[str]
    country: Optional[str]
    city: Optional[str]

    class Config:
        from_attributes = True


class TrackResponse(BaseModel):
    id: str
    recipient: Optional[str]
    subject: Optional[str]
    notes: Optional[str]
    message_group_id: Optional[str] = None
    created_at: datetime
    open_count: int = 0
    pixel_url: str = ""

    class Config:
        from_attributes = True


class TrackDetailResponse(TrackResponse):
    opens: List[OpenResponse] = []


class StatsResponse(BaseModel):
    total_tracks: int
    total_opens: int
    tracks_with_opens: int


@router.get("/tracks", response_model=List[TrackResponse])
async def list_tracks(
    db: AsyncSession = Depends(get_db),
    auth: bool = Depends(verify_api_key)
):
    result = await db.execute(
        select(TrackedEmail).order_by(TrackedEmail.created_at.desc())
    )
    tracks = result.scalars().all()
    
    response = []
    for track in tracks:
        # Count opens
        count_result = await db.execute(
            select(func.count(Open.id)).where(Open.tracked_email_id == track.id)
        )
        open_count = count_result.scalar() or 0
        
        response.append(TrackResponse(
            id=track.id,
            recipient=track.recipient,
            subject=track.subject,
            notes=track.notes,
            message_group_id=track.message_group_id,
            created_at=track.created_at,
            open_count=open_count,
            pixel_url=get_pixel_url(track.id)
        ))
    
    return response


@router.post("/tracks", response_model=TrackResponse)
async def create_track(
    track: TrackCreate,
    db: AsyncSession = Depends(get_db),
    auth: bool = Depends(verify_api_key)
):
    track_id = str(uuid.uuid4())
    
    new_track = TrackedEmail(
        id=track_id,
        recipient=track.recipient,
        subject=track.subject,
        notes=track.notes,
        message_group_id=track.message_group_id
    )
    
    db.add(new_track)
    await db.commit()
    await db.refresh(new_track)
    
    return TrackResponse(
        id=new_track.id,
        recipient=new_track.recipient,
        subject=new_track.subject,
        notes=new_track.notes,
        message_group_id=new_track.message_group_id,
        created_at=new_track.created_at,
        open_count=0,
        pixel_url=get_pixel_url(new_track.id)
    )


@router.get("/tracks/{track_id}", response_model=TrackDetailResponse)
async def get_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    auth: bool = Depends(verify_api_key)
):
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
    
    return TrackDetailResponse(
        id=track.id,
        recipient=track.recipient,
        subject=track.subject,
        notes=track.notes,
        message_group_id=track.message_group_id,
        created_at=track.created_at,
        open_count=len(opens),
        pixel_url=get_pixel_url(track.id),
        opens=[OpenResponse.model_validate(o) for o in opens]
    )


@router.get("/tracks/{track_id}/opens", response_model=List[OpenResponse])
async def get_track_opens(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    auth: bool = Depends(verify_api_key)
):
    result = await db.execute(
        select(Open).where(Open.tracked_email_id == track_id).order_by(Open.opened_at.desc())
    )
    opens = result.scalars().all()
    return [OpenResponse.model_validate(o) for o in opens]


@router.delete("/tracks/{track_id}")
async def delete_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    auth: bool = Depends(verify_api_key)
):
    result = await db.execute(
        select(TrackedEmail).where(TrackedEmail.id == track_id)
    )
    track = result.scalar_one_or_none()
    
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    await db.execute(delete(TrackedEmail).where(TrackedEmail.id == track_id))
    await db.commit()
    
    return {"message": "Track deleted"}


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    auth: bool = Depends(verify_api_key)
):
    # Total tracks
    tracks_result = await db.execute(select(func.count(TrackedEmail.id)))
    total_tracks = tracks_result.scalar() or 0
    
    # Total opens
    opens_result = await db.execute(select(func.count(Open.id)))
    total_opens = opens_result.scalar() or 0
    
    # Tracks with at least one open
    with_opens_result = await db.execute(
        select(func.count(func.distinct(Open.tracked_email_id)))
    )
    tracks_with_opens = with_opens_result.scalar() or 0
    
    return StatsResponse(
        total_tracks=total_tracks,
        total_opens=total_opens,
        tracks_with_opens=tracks_with_opens
    )


class RecentOpenResponse(BaseModel):
    """Response for recent opens endpoint - includes track details for notifications."""
    open_id: int
    opened_at: datetime
    recipient: Optional[str]
    subject: Optional[str]
    country: Optional[str]
    city: Optional[str]
    track_id: str

    class Config:
        from_attributes = True


@router.get("/opens/recent", response_model=List[RecentOpenResponse])
async def get_recent_opens(
    since: Optional[float] = Query(None, description="Unix timestamp to get opens after"),
    db: AsyncSession = Depends(get_db),
    auth: bool = Depends(verify_api_key)
):
    """
    Get recent real opens (excluding proxy opens) since a given timestamp.
    Used by Chrome extension for browser notifications.
    """
    recent_opens = []
    offset = 0
    since_dt = datetime.fromtimestamp(since, tz=timezone.utc) if since is not None else None

    while len(recent_opens) < RECENT_REAL_OPENS_LIMIT:
        query = (
            select(Open, TrackedEmail)
            .join(TrackedEmail, Open.tracked_email_id == TrackedEmail.id)
            .order_by(Open.opened_at.desc())
            .limit(RECENT_OPEN_BATCH_SIZE)
            .offset(offset)
        )

        if since_dt is not None:
            query = query.where(Open.opened_at > since_dt)

        result = await db.execute(query)
        rows = result.all()
        if not rows:
            break

        for open_record, tracked_email in rows:
            proxy_type = detect_proxy_type(open_record.ip_address or "", open_record.user_agent or "")
            if proxy_type is not None:
                continue

            recent_opens.append(RecentOpenResponse(
                open_id=open_record.id,
                opened_at=open_record.opened_at,
                recipient=tracked_email.recipient,
                subject=tracked_email.subject,
                country=open_record.country,
                city=open_record.city,
                track_id=tracked_email.id
            ))

            if len(recent_opens) >= RECENT_REAL_OPENS_LIMIT:
                break

        if len(rows) < RECENT_OPEN_BATCH_SIZE:
            break

        offset += RECENT_OPEN_BATCH_SIZE

    return recent_opens
