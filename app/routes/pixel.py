from fastapi import APIRouter, Request, Depends, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
import base64
import logging

from ..database import get_db
from ..services.tracking import record_pixel_open

logger = logging.getLogger(__name__)

router = APIRouter()

# 1x1 transparent GIF (43 bytes)
PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

@router.get("/p/{tracking_id}.gif")
async def track_pixel(
    tracking_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # Always return pixel regardless of whether tracking_id exists
    # This prevents information leakage

    try:
        await record_pixel_open(db, tracking_id, request, background_tasks)
    except Exception as e:
        # Log the error but don't break pixel delivery
        logger.exception(f"Failed to record open for tracking_id={tracking_id}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass  # Rollback may fail if session is in a bad state

    return Response(
        content=PIXEL_GIF,
        media_type="image/gif",
        background=background_tasks,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
