import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.services import analytics
from app.services.analytics import RealOpenEvent, TrackSnapshot


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 27, 18, 0, tzinfo=timezone.utc)


class AnalyticsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_analytics_context_builds_expected_summary_payload(self) -> None:
        tracks = [
            TrackSnapshot(id="track-1", created_at=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)),
            TrackSnapshot(id="track-2", created_at=datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)),
        ]
        real_opens = [
            RealOpenEvent("track-1", datetime(2026, 3, 27, 14, 0, tzinfo=timezone.utc), "United States", "New York"),
            RealOpenEvent("track-1", datetime(2026, 3, 27, 15, 0, tzinfo=timezone.utc), "United States", "New York"),
            RealOpenEvent("track-2", datetime(2026, 3, 26, 18, 0, tzinfo=timezone.utc), "Canada", "Toronto"),
        ]

        with (
            patch.object(analytics, "datetime", FrozenDateTime),
            patch.object(
                analytics,
                "_load_tracks_and_real_opens",
                AsyncMock(return_value=(tracks, real_opens)),
            ),
        ):
            context = await analytics.build_analytics_context(object(), "invalid")

        self.assertEqual("30", context["date_range"])
        self.assertEqual(2, context["total_emails"])
        self.assertEqual(3, context["total_real_opens"])
        self.assertEqual(100.0, context["open_rate"])
        self.assertEqual("4.0 hrs", context["avg_time_to_open"])
        self.assertEqual(["United States", "Canada"], json.loads(context["country_labels"]))
        self.assertEqual([2, 1], json.loads(context["country_data"]))
        self.assertEqual(3, sum(json.loads(context["hour_data"])))
        self.assertEqual(3, sum(json.loads(context["dow_data"])))
        self.assertEqual(["<1 hr", "1-6 hrs", "6-24 hrs", "1-3 days", "3-7 days", ">7 days"], json.loads(context["time_bucket_labels"]))
        self.assertEqual([0, 1, 1, 0, 0, 0], json.loads(context["time_bucket_data"]))

    async def test_export_analytics_csv_includes_summary_and_breakdowns(self) -> None:
        tracks = [
            TrackSnapshot(id="track-1", created_at=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)),
        ]
        real_opens = [
            RealOpenEvent("track-1", datetime(2026, 3, 27, 13, 0, tzinfo=timezone.utc), "United States", "New York"),
        ]

        with (
            patch.object(analytics, "datetime", FrozenDateTime),
            patch.object(
                analytics,
                "_load_tracks_and_real_opens",
                AsyncMock(return_value=(tracks, real_opens)),
            ),
        ):
            filename, csv_content = await analytics.export_analytics_csv(object(), "7")

        self.assertEqual("mailtrack_analytics_7days_2026-03-27.csv", filename)
        self.assertIn("=== Analytics Summary ===", csv_content)
        self.assertIn("Total Emails Tracked,1", csv_content)
        self.assertIn("Open Rate,100.0%", csv_content)
        self.assertIn("United States,1", csv_content)
        self.assertIn('"New York, United States",1', csv_content)
        self.assertIn("=== Opens by Day of Week ===", csv_content)

    def test_generate_date_keys_handles_weekly_and_monthly_ranges(self) -> None:
        weekly_keys = analytics._generate_date_keys(
            datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
            "weekly",
        )
        monthly_keys = analytics._generate_date_keys(
            datetime(2025, 11, 15, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
            "monthly",
        )

        self.assertEqual(["2026-03-02", "2026-03-09", "2026-03-16"], weekly_keys)
        self.assertEqual(["2025-11", "2025-12", "2026-01", "2026-02"], monthly_keys)

    def test_build_time_series_aligns_opens_and_emails_by_period(self) -> None:
        tracks = [
            TrackSnapshot(id="track-1", created_at=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)),
            TrackSnapshot(id="track-2", created_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)),
        ]
        real_opens = [
            RealOpenEvent("track-1", datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)),
            RealOpenEvent("track-2", datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)),
        ]

        labels, email_counts, open_counts = analytics._build_time_series(
            tracks,
            real_opens,
            datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
            "weekly",
        )

        self.assertEqual(["2026-03-02", "2026-03-09", "2026-03-16"], labels)
        self.assertEqual([1, 1, 0], email_counts)
        self.assertEqual([1, 1, 0], open_counts)
