import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse

from app import main


class FakeTask:
    def __init__(self) -> None:
        self.cancelled = False
        self.awaited = False

    def cancel(self) -> None:
        self.cancelled = True

    def __await__(self):
        async def _wait():
            self.awaited = True
            raise asyncio.CancelledError

        return _wait().__await__()


class MainTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_reminder_task_logs_traceback_on_failure(self) -> None:
        check_followup_reminders = AsyncMock(side_effect=RuntimeError("boom"))
        sleep = AsyncMock(side_effect=asyncio.CancelledError())

        with (
            patch.object(main, "check_followup_reminders", check_followup_reminders),
            patch.object(main.asyncio, "sleep", sleep),
            patch.object(main.logger, "exception") as logger_exception,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await main.followup_reminder_task()

        check_followup_reminders.assert_awaited_once()
        logger_exception.assert_called_once()

    async def test_lifespan_runs_startup_and_cancels_background_task(self) -> None:
        fake_task = FakeTask()
        init_database = AsyncMock()
        init_geoip = AsyncMock()

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with (
            patch.object(main, "init_database", init_database),
            patch.object(main, "init_geoip", init_geoip),
            patch.object(main.asyncio, "create_task", side_effect=fake_create_task),
        ):
            async with main.lifespan(main.app):
                pass

        init_database.assert_awaited_once()
        init_geoip.assert_awaited_once()
        self.assertTrue(fake_task.cancelled)
        self.assertTrue(fake_task.awaited)

    async def test_health_check_returns_503_when_database_is_unavailable(self) -> None:
        with patch.object(main, "check_database_health", AsyncMock(return_value=(False, "down"))):
            response = await main.health_check()

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(503, response.status_code)
        self.assertEqual(b'{"status":"error","database":"unavailable"}', response.body)

    async def test_health_check_returns_ok_payload_when_database_is_available(self) -> None:
        with patch.object(main, "check_database_health", AsyncMock(return_value=(True, None))):
            response = await main.health_check()

        self.assertEqual({"status": "ok", "database": "ok"}, response)
