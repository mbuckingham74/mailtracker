import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import BackgroundTasks, Request

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("BASE_URL", "https://example.com")
os.environ.setdefault("DASHBOARD_USERNAME", "test-user")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password")

from app.services import tracking


class FakeResult:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class FakeAsyncSession:
    def __init__(self, row):
        self.row = row
        self.added = []
        self.queries = []
        self.commits = 0

    async def execute(self, query):
        self.queries.append(query)
        return FakeResult(self.row)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


class FrozenDateTime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc)


class TrackingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_pixel_open_sets_opened_at_in_utc_before_insert(self) -> None:
        frozen_now = FrozenDateTime.now()
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "https",
                "path": "/p/track-1.gif",
                "raw_path": b"/p/track-1.gif",
                "query_string": b"",
                "root_path": "",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 443),
            }
        )
        db = FakeAsyncSession(
            (
                "alice@example.com",
                "Hello",
                frozen_now - timedelta(seconds=10),
                None,
                None,
                None,
            )
        )

        with (
            patch.object(tracking, "datetime", FrozenDateTime),
            patch.object(tracking, "get_client_ip", return_value="8.8.8.8"),
            patch.object(tracking, "classify_open", return_value=(True, None)),
            patch.object(tracking, "lookup_ip", return_value=("United States", "New York")),
            patch.object(tracking, "is_email_notifications_enabled", return_value=False),
        ):
            await tracking.record_pixel_open(
                db,
                "track-1",
                request,
                BackgroundTasks(),
            )

        self.assertEqual(1, db.commits)
        self.assertEqual(1, len(db.added))
        self.assertEqual(frozen_now, db.added[0].opened_at)


if __name__ == "__main__":
    unittest.main()
