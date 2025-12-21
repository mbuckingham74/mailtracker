from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
import os

from ..database import get_db, TrackedEmail, Open

router = APIRouter(prefix="/api")

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("Required environment variable API_KEY is not set")

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("Required environment variable BASE_URL is not set (e.g., https://mailtrack.example.com)")


def get_pixel_url(track_id: str) -> str:
    """Generate absolute pixel URL for a track."""
    return f"{BASE_URL}/p/{track_id}.gif"


# Auth dependency
async def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
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
