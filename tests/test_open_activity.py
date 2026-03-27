import unittest
from datetime import datetime, timezone

from app.services.open_activity import (
    load_track_open_records,
    load_track_open_records_map,
    load_track_open_summaries,
)


class FakeAsyncSession:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return self.rows


class OpenActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_track_open_records_map_builds_shared_record_shape(self) -> None:
        opened_at = datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc)
        db = FakeAsyncSession([
            (
                "track-1",
                42,
                opened_at,
                True,
                None,
                "8.8.8.8",
                "Mozilla/5.0",
                "https://mail.google.com/",
                "United States",
                "New York",
            ),
        ])

        records_by_track_id = await load_track_open_records_map(db, ["track-1"])

        self.assertEqual(["track-1"], list(records_by_track_id.keys()))
        record = records_by_track_id["track-1"][0]
        self.assertEqual("track-1", record.tracked_email_id)
        self.assertEqual(42, record.id)
        self.assertEqual(opened_at, record.opened_at)
        self.assertEqual("8.8.8.8", record.ip_address)
        self.assertEqual("Mozilla/5.0", record.user_agent)
        self.assertEqual("https://mail.google.com/", record.referer)
        self.assertEqual("United States", record.country)
        self.assertEqual("New York", record.city)
        self.assertIsNone(record.proxy_type)
        self.assertTrue(record.is_real_open)

    async def test_load_track_open_records_returns_single_track_list(self) -> None:
        opened_at = datetime(2026, 3, 27, 19, 0, tzinfo=timezone.utc)
        db = FakeAsyncSession([
            (
                "track-1",
                99,
                opened_at,
                False,
                "apple",
                "17.1.2.3",
                "Mozilla/5.0",
                "",
                None,
                None,
            ),
        ])

        records = await load_track_open_records(db, "track-1", order="desc")

        self.assertEqual(1, len(records))
        self.assertEqual(99, records[0].id)
        self.assertEqual("apple", records[0].proxy_type)
        self.assertFalse(records[0].is_real_open)

    async def test_load_track_open_summaries_counts_proxy_and_real_firsts(self) -> None:
        proxy_opened_at = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
        real_opened_at = datetime(2026, 3, 27, 13, 0, tzinfo=timezone.utc)
        unknown_proxy_opened_at = datetime(2026, 3, 27, 14, 0, tzinfo=timezone.utc)
        db = FakeAsyncSession([
            ("track-1", proxy_opened_at, False, "apple"),
            ("track-1", real_opened_at, True, None),
            ("track-2", unknown_proxy_opened_at, False, None),
        ])

        summaries = await load_track_open_summaries(
            db,
            track_ids=["track-1", "track-2"],
        )

        self.assertEqual(2, summaries["track-1"].open_count)
        self.assertEqual(1, summaries["track-1"].real_open_count)
        self.assertEqual(proxy_opened_at, summaries["track-1"].first_open)
        self.assertEqual(real_opened_at, summaries["track-1"].first_real_open)
        self.assertEqual(proxy_opened_at, summaries["track-1"].first_proxy_open)
        self.assertEqual("apple", summaries["track-1"].first_proxy_type)

        self.assertEqual(1, summaries["track-2"].open_count)
        self.assertEqual(0, summaries["track-2"].real_open_count)
        self.assertEqual(unknown_proxy_opened_at, summaries["track-2"].first_open)
        self.assertIsNone(summaries["track-2"].first_proxy_open)
        self.assertIsNone(summaries["track-2"].first_proxy_type)


if __name__ == "__main__":
    unittest.main()
