import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import pixel as pixel_routes


class FakeAsyncSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1


class RoutesPixelTests(unittest.TestCase):
    def _build_client(self):
        app = FastAPI()
        app.include_router(pixel_routes.router)
        fake_db = FakeAsyncSession()

        async def override_db():
            return fake_db

        app.dependency_overrides[pixel_routes.get_db] = override_db
        return TestClient(app), fake_db

    def test_track_pixel_returns_gif_even_when_recording_fails(self) -> None:
        client, fake_db = self._build_client()
        record_pixel_open = AsyncMock(side_effect=RuntimeError("boom"))

        with patch.object(pixel_routes, "record_pixel_open", record_pixel_open):
            response = client.get("/p/track-1.gif")

        self.assertEqual(200, response.status_code)
        self.assertEqual("image/gif", response.headers["content-type"])
        self.assertEqual(pixel_routes.PIXEL_GIF, response.content)
        self.assertEqual(1, fake_db.rollback_count)
        record_pixel_open.assert_awaited_once()
