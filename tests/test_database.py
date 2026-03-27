import unittest

from app.database import _backfill_missing_open_classification


class FakeResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


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
