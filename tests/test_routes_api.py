import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import api as api_routes


class RoutesApiTests(unittest.TestCase):
    def _build_client(self):
        app = FastAPI()
        app.include_router(api_routes.router)
        fake_db = object()

        async def override_db():
            return fake_db

        app.dependency_overrides[api_routes.get_db] = override_db
        return TestClient(app), fake_db

    def test_stats_requires_api_key(self) -> None:
        client, _fake_db = self._build_client()

        response = client.get("/api/stats")

        self.assertEqual(401, response.status_code)
        self.assertEqual({"detail": "Invalid API key"}, response.json())

    def test_recent_opens_rejects_out_of_range_since_timestamp(self) -> None:
        client, _fake_db = self._build_client()

        response = client.get(
            "/api/opens/recent",
            params={"since": "1e20"},
            headers={"X-API-Key": "test-api-key"},
        )

        self.assertEqual(422, response.status_code)
        self.assertEqual({"detail": "Invalid 'since' timestamp"}, response.json())

    def test_stats_returns_service_payload(self) -> None:
        client, fake_db = self._build_client()
        get_api_stats = AsyncMock(
            return_value={
                "total_tracks": 12,
                "total_opens": 34,
                "tracks_with_opens": 5,
                "latest_real_open": None,
            }
        )

        with patch.object(api_routes, "get_api_stats", get_api_stats):
            response = client.get(
                "/api/stats",
                headers={"X-API-Key": "test-api-key"},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "total_tracks": 12,
                "total_opens": 34,
                "tracks_with_opens": 5,
                "latest_real_open": None,
            },
            response.json(),
        )
        get_api_stats.assert_awaited_once_with(fake_db)
