import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, Request
from sqlalchemy.exc import OperationalError

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
        self.flushes = 0
        self.rollbacks = 0

    async def execute(self, query):
        self.queries.append(query)
        return FakeResult(self.row)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class SequenceAsyncSession:
    def __init__(self, rows):
        self.rows = list(rows)
        self.added = []
        self.queries = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0

    async def execute(self, query):
        self.queries.append(query)
        if not self.rows:
            return FakeResult(None)
        return FakeResult(self.rows.pop(0))

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class RetryOnceAsyncSession(FakeAsyncSession):
    def __init__(self, row, exc):
        super().__init__(row)
        self.exc = exc
        self.failed_once = False

    async def execute(self, query):
        self.queries.append(query)
        if not self.failed_once:
            self.failed_once = True
            raise self.exc
        return FakeResult(self.row)


class FrozenDateTime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc)


class TrackingServiceTests(unittest.IsolatedAsyncioTestCase):
    def _build_request(self) -> Request:
        return Request(
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

    async def test_record_pixel_open_sets_opened_at_in_utc_before_insert(self) -> None:
        frozen_now = FrozenDateTime.now()
        request = self._build_request()
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
        self.assertIn("FOR UPDATE", str(db.queries[0]))

    async def test_record_pixel_open_returns_early_for_missing_track(self) -> None:
        db = FakeAsyncSession(None)

        await tracking.record_pixel_open(
            db,
            "missing",
            self._build_request(),
            BackgroundTasks(),
        )

        self.assertEqual(0, db.commits)
        self.assertEqual([], db.added)

    async def test_record_pixel_open_returns_early_for_recently_created_track(self) -> None:
        frozen_now = FrozenDateTime.now()
        db = FakeAsyncSession(
            (
                "alice@example.com",
                "Hello",
                frozen_now - timedelta(seconds=2),
                None,
                None,
                None,
            )
        )

        with patch.object(tracking, "datetime", FrozenDateTime):
            await tracking.record_pixel_open(
                db,
                "track-1",
                self._build_request(),
                BackgroundTasks(),
            )

        self.assertEqual(0, db.commits)
        self.assertEqual([], db.added)

    async def test_record_pixel_open_does_not_flush_or_schedule_background_tasks_for_proxy_open(self) -> None:
        frozen_now = FrozenDateTime.now()
        db = SequenceAsyncSession(
            [
                (
                    "alice@example.com",
                    "Hello",
                    frozen_now - timedelta(seconds=10),
                    None,
                    None,
                    None,
                )
            ]
        )
        background_tasks = BackgroundTasks()

        with (
            patch.object(tracking, "datetime", FrozenDateTime),
            patch.object(tracking, "get_client_ip", return_value="66.102.1.1"),
            patch.object(tracking, "classify_open", return_value=(False, "google")),
            patch.object(tracking, "lookup_ip", return_value=(None, None)),
            patch.object(tracking, "is_email_notifications_enabled", return_value=True),
        ):
            await tracking.record_pixel_open(
                db,
                "track-1",
                self._build_request(),
                background_tasks,
            )

        self.assertEqual(1, db.commits)
        self.assertEqual(0, db.flushes)
        self.assertEqual(0, len(background_tasks.tasks))
        self.assertFalse(db.added[0].is_real_open)
        self.assertEqual("google", db.added[0].proxy_type)

    async def test_record_pixel_open_schedules_all_real_open_notifications(self) -> None:
        frozen_now = FrozenDateTime.now()
        db = SequenceAsyncSession(
            [
                (
                    "alice@example.com",
                    "Hello",
                    frozen_now - timedelta(days=30),
                    None,
                    None,
                    None,
                ),
                None,
            ]
        )
        background_tasks = BackgroundTasks()
        load_recent_real_open_count = AsyncMock(return_value=3)
        load_first_real_open_at = AsyncMock(return_value=frozen_now - timedelta(days=20))

        with (
            patch.object(tracking, "datetime", FrozenDateTime),
            patch.object(tracking, "get_client_ip", return_value="8.8.8.8"),
            patch.object(tracking, "classify_open", return_value=(True, None)),
            patch.object(tracking, "lookup_ip", return_value=("United States", "New York")),
            patch.object(tracking, "is_email_notifications_enabled", return_value=True),
            patch.object(tracking, "_load_recent_real_open_count", load_recent_real_open_count),
            patch.object(tracking, "_load_first_real_open_at", load_first_real_open_at),
        ):
            await tracking.record_pixel_open(
                db,
                "track-1",
                self._build_request(),
                background_tasks,
            )

        self.assertEqual(1, db.commits)
        self.assertEqual(1, db.flushes)
        self.assertEqual(3, len(background_tasks.tasks))
        self.assertEqual(
            [
                tracking.send_open_notification,
                tracking.send_hot_conversation_notification,
                tracking.send_revived_conversation_notification,
            ],
            [task.func for task in background_tasks.tasks],
        )
        update_params = db.queries[1].compile().params
        self.assertIn("track-1", update_params.values())
        self.assertEqual(
            ["recipient", "subject", "opened_at", "country", "city", "track_id", "sent_at"],
            list(background_tasks.tasks[0].kwargs.keys()),
        )

    async def test_record_pixel_open_retries_transient_mysql_deadlock(self) -> None:
        frozen_now = FrozenDateTime.now()
        deadlock_error = OperationalError(
            "SELECT ... FOR UPDATE",
            {},
            RuntimeError(1213, "Deadlock found when trying to get lock; try restarting transaction"),
        )
        db = RetryOnceAsyncSession(
            (
                "alice@example.com",
                "Hello",
                frozen_now - timedelta(seconds=10),
                None,
                None,
                None,
            ),
            deadlock_error,
        )

        with (
            patch.object(tracking, "datetime", FrozenDateTime),
            patch.object(tracking, "get_client_ip", return_value="8.8.8.8"),
            patch.object(tracking, "classify_open", return_value=(True, None)),
            patch.object(tracking, "lookup_ip", return_value=("United States", "New York")),
            patch.object(tracking, "is_email_notifications_enabled", return_value=False),
            patch.object(tracking.asyncio, "sleep", AsyncMock()),
        ):
            await tracking.record_pixel_open(
                db,
                "track-1",
                self._build_request(),
                BackgroundTasks(),
            )

        self.assertEqual(1, db.rollbacks)
        self.assertEqual(1, db.commits)
        self.assertEqual(1, len(db.added))


if __name__ == "__main__":
    unittest.main()
