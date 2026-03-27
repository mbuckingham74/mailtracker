from .config import settings


def get_pixel_url(track_id: str) -> str:
    return f"{settings.base_url}/p/{track_id}.gif"
