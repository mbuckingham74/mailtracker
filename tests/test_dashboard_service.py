import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.open_snapshot import build_open_snapshot
from app.services import dashboard
from app.services.dashboard import DashboardTrackSnapshot, DetailTrackSnapshot
from app.services.open_activity import TrackOpenRecord, TrackOpenSummary


class FakeResult:
    def __init__(self, *, row=None, scalar=None, rows=None) -> None:
        self.row = row
        self.scalar_value = scalar
        self.rows = rows or []

    def one_or_none(self):
        return self.row

    def scalar_one_or_none(self):
        return self.scalar_value

    def __iter__(self):
        return iter(self.rows)


class FakeAsyncSession:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.queries = []
        self.commit_count = 0

    async def execute(self, query):
        self.queries.append(query)
        if not self.results:
            raise AssertionError("Unexpected execute() call")
        return self.results.pop(0)

    async def commit(self) -> None:
        self.commit_count += 1


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 27, 18, 0, tzinfo=timezone.utc)


def make_open_record(
    *,
    tracked_email_id: str,
    open_id: int,
    opened_at: datetime,
    is_real_open: bool,
    proxy_type: str | None = None,
) -> TrackOpenRecord:
    return build_open_snapshot(
        TrackOpenRecord,
        tracked_email_id=tracked_email_id,
        id=open_id,
        referer="https://mail.google.com/",
        opened_at=opened_at,
        ip_address="8.8.8.8",
        user_agent="Mozilla/5.0",
        country="United States",
        city="New York",
        proxy_type=proxy_type,
        is_real_open=is_real_open,
    )


class DashboardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_dashboard_context_groups_tracks_and_sorts_pinned_first(self) -> None:
        tracks = [
            DashboardTrackSnapshot(
                id="track-1",
                recipient="alice@example.com",
                subject="Group subject",
                notes=None,
                message_group_id="group-1",
                created_at=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
                pinned=False,
            ),
            DashboardTrackSnapshot(
                id="track-2",
                recipient="bob@example.com",
                subject="Group subject",
                notes=None,
                message_group_id="group-1",
                created_at=datetime(2026, 3, 27, 11, 0, tzinfo=timezone.utc),
                pinned=False,
            ),
            DashboardTrackSnapshot(
                id="track-3",
                recipient="carol@example.com",
                subject="Pinned single",
                notes="follow up",
                message_group_id=None,
                created_at=datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc),
                pinned=True,
            ),
        ]
        open_summaries = {
            "track-1": TrackOpenSummary(open_count=2, real_open_count=1),
            "track-2": TrackOpenSummary(open_count=0, real_open_count=0),
            "track-3": TrackOpenSummary(open_count=1, real_open_count=1),
        }

        with (
            patch.object(
                dashboard,
                "_load_dashboard_track_snapshots",
                AsyncMock(return_value=tracks),
            ),
            patch.object(
                dashboard,
                "load_track_open_summaries",
                AsyncMock(return_value=open_summaries),
            ),
        ):
            context = await dashboard.build_dashboard_context(
                object(),
                filter_value="invalid",
                search="  alice  ",
                date_range="invalid",
                page=99,
            )

        self.assertEqual("all", context["filter"])
        self.assertEqual("alice", context["search"])
        self.assertEqual("all", context["date_range"])
        self.assertEqual(1, context["page"])
        self.assertEqual(2, context["total_items"])
        self.assertEqual("track-3", context["tracks"][0]["track"].id)
        self.assertTrue(context["tracks"][1]["is_group"])
        self.assertEqual(2, context["tracks"][1]["total_opens"])
        self.assertEqual(1, context["tracks"][1]["total_real_opens"])
        self.assertEqual({"search": "alice"}, context["query_params"])

    async def test_build_dashboard_context_filters_opened_and_unopened_tracks(self) -> None:
        tracks = [
            DashboardTrackSnapshot(
                id="opened",
                recipient="opened@example.com",
                subject="Opened",
                notes=None,
                message_group_id=None,
                created_at=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
            ),
            DashboardTrackSnapshot(
                id="unopened",
                recipient="unopened@example.com",
                subject="Unopened",
                notes=None,
                message_group_id=None,
                created_at=datetime(2026, 3, 27, 11, 0, tzinfo=timezone.utc),
            ),
        ]
        open_summaries = {
            "opened": TrackOpenSummary(open_count=1, real_open_count=1),
            "unopened": TrackOpenSummary(open_count=1, real_open_count=0),
        }

        with (
            patch.object(dashboard, "_load_dashboard_track_snapshots", AsyncMock(return_value=tracks)),
            patch.object(dashboard, "load_track_open_summaries", AsyncMock(return_value=open_summaries)),
        ):
            opened_context = await dashboard.build_dashboard_context(
                object(),
                filter_value="opened",
                search="",
                date_range="all",
                page=1,
            )
            unopened_context = await dashboard.build_dashboard_context(
                object(),
                filter_value="unopened",
                search="",
                date_range="all",
                page=1,
            )

        self.assertEqual(["opened"], [item["track"].id for item in opened_context["tracks"]])
        self.assertEqual(["unopened"], [item["track"].id for item in unopened_context["tracks"]])

    async def test_build_detail_context_returns_proxy_and_real_open_details(self) -> None:
        db = FakeAsyncSession(
            [
                FakeResult(
                    row=(
                        "track-1",
                        "alice@example.com, bob@example.com",
                        "Quarterly Update",
                        "notes",
                        datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
                    )
                ),
            ]
        )
        opens_asc = [
            make_open_record(
                tracked_email_id="track-1",
                open_id=1,
                opened_at=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
                is_real_open=False,
                proxy_type="apple",
            ),
            make_open_record(
                tracked_email_id="track-1",
                open_id=2,
                opened_at=datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc),
                is_real_open=True,
            ),
        ]

        with patch.object(dashboard, "load_track_open_records", AsyncMock(return_value=opens_asc)):
            context = await dashboard.build_detail_context(db, "track-1")

        self.assertEqual("track-1", context["track"].id)
        self.assertEqual(1, context["real_open_count"])
        self.assertEqual("apple", context["first_proxy_type"])
        self.assertEqual([2, 1], [open_record.id for open_record in context["opens"]])
        self.assertIn("/p/track-1.gif", context["pixel_url"])
        self.assertIn("to%3Aalice%40example.com", context["gmail_search_url"])
        self.assertIn("subject%3AQuarterly%20Update", context["gmail_search_url"])

    async def test_build_detail_context_raises_404_for_missing_track(self) -> None:
        db = FakeAsyncSession([FakeResult(row=None)])

        with self.assertRaises(HTTPException) as exc:
            await dashboard.build_detail_context(db, "missing")

        self.assertEqual(404, exc.exception.status_code)

    async def test_toggle_track_pin_updates_existing_track_and_skips_missing_track(self) -> None:
        existing_db = FakeAsyncSession([FakeResult(scalar=False), FakeResult()])
        missing_db = FakeAsyncSession([FakeResult(scalar=None)])

        await dashboard.toggle_track_pin(existing_db, "track-1")
        await dashboard.toggle_track_pin(missing_db, "missing")

        self.assertEqual(1, existing_db.commit_count)
        self.assertEqual(0, missing_db.commit_count)
        self.assertEqual(2, len(existing_db.queries))
        self.assertIn(True, existing_db.queries[1].compile().params.values())

    async def test_update_track_notes_and_delete_track_commit_changes(self) -> None:
        db = FakeAsyncSession([FakeResult(), FakeResult()])

        await dashboard.update_track_notes(db, "track-1", "   ")
        await dashboard.delete_track(db, "track-1")

        self.assertEqual(2, db.commit_count)
        self.assertIn(None, db.queries[0].compile().params.values())

    async def test_export_tracks_csv_includes_real_open_flag_and_metadata(self) -> None:
        tracks = [
            DashboardTrackSnapshot(
                id="track-1",
                recipient="alice@example.com",
                subject="Hello",
                notes=None,
                message_group_id=None,
                created_at=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
                pinned=False,
            ),
        ]
        opens_by_track_id = {
            "track-1": [
                make_open_record(
                    tracked_email_id="track-1",
                    open_id=1,
                    opened_at=datetime(2026, 3, 27, 13, 0, tzinfo=timezone.utc),
                    is_real_open=True,
                )
            ]
        }

        with (
            patch.object(dashboard, "datetime", FrozenDateTime),
            patch.object(dashboard, "_load_dashboard_track_snapshots", AsyncMock(return_value=tracks)),
            patch.object(dashboard, "load_track_open_records_map", AsyncMock(return_value=opens_by_track_id)),
        ):
            filename, csv_content = await dashboard.export_tracks_csv(object())

        self.assertEqual("mailtrack_export_2026-03-27.csv", filename)
        self.assertIn("email_id,recipient,subject,email_created_at,opened_at,ip_address,country,city,user_agent,proxy_type,is_real_open", csv_content)
        self.assertIn("track-1,alice@example.com,Hello", csv_content)
        self.assertIn(",yes", csv_content)
