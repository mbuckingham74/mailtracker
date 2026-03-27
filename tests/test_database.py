import unittest
from unittest.mock import AsyncMock, patch

from app import database
from app.database import _backfill_missing_open_classification


class FakeResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self, row_batches) -> None:
        self.row_batches = list(row_batches)
        self.updates = []

    async def execute(self, statement, params=None):
        if params is None:
            if not self.row_batches:
                return FakeResult([])
            return FakeResult(self.row_batches.pop(0))

        self.updates.append(params)
        return None


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_missing_open_classification_updates_legacy_rows(self) -> None:
        conn = FakeConnection(
            [
                [
                    (1, None, "66.102.1.1", ""),
                    (2, None, "8.8.8.8", "Mozilla/5.0"),
                ],
                [],
            ]
        )

        await _backfill_missing_open_classification(conn)

        self.assertEqual(
            [
                [
                    {
                        "target_open_id": 1,
                        "is_real_open": False,
                        "proxy_type": "google",
                    },
                    {
                        "target_open_id": 2,
                        "is_real_open": True,
                        "proxy_type": None,
                    },
                ]
            ],
            conn.updates,
        )

    async def test_init_database_applies_missing_migrations_and_indexes(self) -> None:
        class FakeInitConnection:
            def __init__(self) -> None:
                self.run_sync_calls = []
                self.ddl_statements = []

            async def run_sync(self, fn) -> None:
                self.run_sync_calls.append(fn)

            async def execute(self, statement, params=None):
                sql = str(statement)
                if "INFORMATION_SCHEMA.COLUMNS" in sql:
                    rows = {
                        "tracked_emails": [("hot_notified_at",)],
                        "opens": [("proxy_type",)],
                    }[params["table_name"]]
                    return FakeResult(rows)
                if "INFORMATION_SCHEMA.STATISTICS" in sql:
                    rows = {
                        "tracked_emails": [],
                        "opens": [("ix_opens_opened_at_id",)],
                    }[params["table_name"]]
                    return FakeResult(rows)

                self.ddl_statements.append(sql.strip())
                return FakeResult([])

        class FakeBeginContext:
            def __init__(self, conn) -> None:
                self.conn = conn

            async def __aenter__(self):
                return self.conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeEngine:
            def __init__(self, conn) -> None:
                self.conn = conn

            def begin(self):
                return FakeBeginContext(self.conn)

        conn = FakeInitConnection()
        backfill = AsyncMock()

        with (
            patch.object(database, "engine", FakeEngine(conn)),
            patch.object(database, "_backfill_missing_open_classification", backfill),
        ):
            await database.init_database()

        self.assertEqual(1, len(conn.run_sync_calls))
        self.assertTrue(
            any("ADD COLUMN revived_notified_at" in sql for sql in conn.ddl_statements)
        )
        self.assertTrue(
            any("ADD COLUMN is_real_open" in sql for sql in conn.ddl_statements)
        )
        self.assertFalse(
            any("ADD COLUMN proxy_type" in sql for sql in conn.ddl_statements)
        )
        self.assertTrue(
            any("CREATE INDEX ix_tracked_emails_created_at" in sql for sql in conn.ddl_statements)
        )
        self.assertFalse(
            any("CREATE INDEX ix_opens_opened_at_id" in sql for sql in conn.ddl_statements)
        )
        backfill.assert_awaited_once_with(conn)

    async def test_check_database_health_returns_true_when_queries_succeed(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.executions = 0

            async def execute(self, query) -> None:
                self.executions += 1

        class FakeSessionContext:
            def __init__(self, session) -> None:
                self.session = session

            async def __aenter__(self):
                return self.session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSessionFactory:
            def __init__(self, session) -> None:
                self.session = session

            def __call__(self):
                return FakeSessionContext(self.session)

        session = FakeSession()

        with patch.object(database, "async_session", FakeSessionFactory(session)):
            healthy, error = await database.check_database_health()

        self.assertTrue(healthy)
        self.assertIsNone(error)
        self.assertEqual(2, session.executions)

    async def test_check_database_health_returns_error_message_when_query_fails(self) -> None:
        class FakeSession:
            async def execute(self, query) -> None:
                raise RuntimeError("database unavailable")

        class FakeSessionContext:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch.object(database, "async_session", lambda: FakeSessionContext()):
            healthy, error = await database.check_database_health()

        self.assertFalse(healthy)
        self.assertEqual("database unavailable", error)
