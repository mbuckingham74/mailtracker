import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("BASE_URL", "https://example.com")
os.environ.setdefault("DASHBOARD_USERNAME", "test-user")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password")

from app.services.api import get_stats


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class SequenceAsyncSession:
    def __init__(self, results):
        self.results = list(results)
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        if not self.results:
            raise AssertionError("Unexpected execute() call")
        return self.results.pop(0)


class ApiServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_stats_includes_latest_real_open_metadata(self) -> None:
        opened_at = datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc)
        db = SequenceAsyncSession(
            [
                ScalarResult(12),
                ScalarResult(34),
                ScalarResult(5),
                [
                    (
                        99,
                        opened_at,
                        "United States",
                        "New York",
                        "8.8.8.8",
                        "Mozilla/5.0",
                        "track-1",
                        "alice@example.com",
                        "Hello",
                    ),
                ],
            ]
        )

        stats = await get_stats(db)

        self.assertEqual(12, stats["total_tracks"])
        self.assertEqual(34, stats["total_opens"])
        self.assertEqual(5, stats["tracks_with_opens"])
        self.assertEqual(
            {
                "open_id": 99,
                "opened_at": opened_at,
                "recipient": "alice@example.com",
                "subject": "Hello",
                "country": "United States",
                "city": "New York",
            },
            stats["latest_real_open"],
        )

    async def test_get_stats_returns_none_when_no_real_opens_exist(self) -> None:
        db = SequenceAsyncSession(
            [
                ScalarResult(12),
                ScalarResult(34),
                ScalarResult(5),
                [],
            ]
        )

        stats = await get_stats(db)

        self.assertIsNone(stats["latest_real_open"])


if __name__ == "__main__":
    unittest.main()
