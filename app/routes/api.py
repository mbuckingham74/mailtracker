from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone

from ..config import settings
from ..database import get_db
from ..services.api import (
    create_track as create_track_record,
    delete_track as delete_track_record,
    get_stats as get_api_stats,
    get_track_with_opens,
    list_track_opens as list_track_open_records,
    list_tracks as list_track_summaries,
)
from ..services.open_activity import (
    RecentRealOpenRecord,
    TrackOpenRecord,
    load_recent_real_open_records,
)
from ..urls import get_pixel_url

router = APIRouter(prefix="/api")

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
    proxy_type: Optional[str] = None
    is_real_open: bool

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
    opens: List[OpenResponse] = Field(default_factory=list)


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


class LatestRealOpenResponse(BaseModel):
    open_id: int
    opened_at: datetime
    recipient: Optional[str]
    subject: Optional[str]
    country: Optional[str]
    city: Optional[str]

    class Config:
        from_attributes = True


class StatsResponse(BaseModel):
    total_tracks: int
    total_opens: int
    tracks_with_opens: int
    latest_real_open: Optional[LatestRealOpenResponse] = None


def _build_open_response(open_record: TrackOpenRecord) -> OpenResponse:
    return OpenResponse(
        id=open_record.id,
        opened_at=open_record.opened_at,
        ip_address=open_record.ip_address,
        user_agent=open_record.user_agent,
        referer=open_record.referer,
        country=open_record.country,
        city=open_record.city,
        proxy_type=open_record.proxy_type,
        is_real_open=open_record.is_real_open,
    )


def _build_track_response_fields(
    track,
    *,
    open_count: int,
) -> dict[str, object]:
    return {
        "id": track.id,
        "recipient": track.recipient,
        "subject": track.subject,
        "notes": track.notes,
        "message_group_id": track.message_group_id,
        "created_at": track.created_at,
        "open_count": open_count,
        "pixel_url": get_pixel_url(track.id),
    }


def _build_recent_open_response(open_record: RecentRealOpenRecord) -> "RecentOpenResponse":
    return RecentOpenResponse(
        open_id=open_record.id,
        opened_at=open_record.opened_at,
        recipient=open_record.recipient,
        subject=open_record.subject,
        country=open_record.country,
        city=open_record.city,
        track_id=open_record.tracked_email_id,
    )


@router.get("/tracks", response_model=List[TrackResponse])
async def list_tracks(
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_api_key)
):
    track_rows = await list_track_summaries(db)
    return [
        TrackResponse(**_build_track_response_fields(track, open_count=open_count))
        for track, open_count in track_rows
    ]


@router.post("/tracks", response_model=TrackResponse)
async def create_track(
    track: TrackCreate,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_api_key)
):
    new_track = await create_track_record(
        db,
        recipient=track.recipient,
        subject=track.subject,
        notes=track.notes,
        message_group_id=track.message_group_id,
    )

    return TrackResponse(**_build_track_response_fields(new_track, open_count=0))


@router.get("/tracks/{track_id}", response_model=TrackDetailResponse)
async def get_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_api_key)
):
    track, opens = await get_track_with_opens(db, track_id)
    return TrackDetailResponse(
        **_build_track_response_fields(track, open_count=len(opens)),
        opens=[_build_open_response(open_record) for open_record in opens],
    )


@router.get("/tracks/{track_id}/opens", response_model=List[OpenResponse])
async def get_track_opens(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_api_key)
):
    opens = await list_track_open_records(db, track_id)
    return [_build_open_response(open_record) for open_record in opens]


@router.delete("/tracks/{track_id}")
async def delete_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_api_key)
):
    await delete_track_record(db, track_id)
    return {"message": "Track deleted"}


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_api_key)
):
    return StatsResponse(**await get_api_stats(db))


@router.get("/opens/recent", response_model=List[RecentOpenResponse])
async def get_recent_opens(
    since: Optional[float] = Query(None, description="Unix timestamp to get opens after"),
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_api_key)
):
    """
    Get recent real opens (excluding proxy opens) since a given timestamp.
    Used by Chrome extension for browser notifications.
    """
    since_dt = datetime.fromtimestamp(since, tz=timezone.utc) if since is not None else None
    recent_opens = await load_recent_real_open_records(db, cutoff=since_dt)
    return [_build_recent_open_response(open_record) for open_record in recent_opens]
