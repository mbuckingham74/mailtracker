import unittest
from dataclasses import dataclass
from datetime import datetime, timezone

from app.open_snapshot import (
    StoredOpenSnapshot,
    build_open_snapshot,
    build_stored_open_snapshot,
)


@dataclass(frozen=True)
class ExtendedOpenSnapshot(StoredOpenSnapshot):
    open_id: int
    referer: str | None


class OpenSnapshotTests(unittest.TestCase):
    def test_build_stored_open_snapshot_coerces_missing_real_flag_to_false(self) -> None:
        snapshot = build_stored_open_snapshot(
            opened_at=None,
            ip_address="8.8.8.8",
            user_agent="Mozilla/5.0",
            is_real_open=None,
        )

        self.assertFalse(snapshot.is_real_open)
        self.assertIsNone(snapshot.proxy_type)

    def test_build_open_snapshot_applies_stored_and_extra_fields(self) -> None:
        opened_at = datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc)

        snapshot = build_open_snapshot(
            ExtendedOpenSnapshot,
            open_id=42,
            referer="https://mail.google.com/",
            opened_at=opened_at,
            ip_address="8.8.8.8",
            user_agent="Mozilla/5.0",
            country="United States",
            city="New York",
            proxy_type=None,
            is_real_open=True,
        )

        self.assertEqual(42, snapshot.open_id)
        self.assertEqual("https://mail.google.com/", snapshot.referer)
        self.assertEqual(opened_at, snapshot.opened_at)
        self.assertEqual("8.8.8.8", snapshot.ip_address)
        self.assertEqual("Mozilla/5.0", snapshot.user_agent)
        self.assertEqual("United States", snapshot.country)
        self.assertEqual("New York", snapshot.city)
        self.assertIsNone(snapshot.proxy_type)
        self.assertTrue(snapshot.is_real_open)


if __name__ == "__main__":
    unittest.main()
