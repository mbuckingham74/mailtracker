import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.services import followups


class FakeAsyncSession:
    def __init__(self) -> None:
        self.queries = []
        self.commit_count = 0

    async def execute(self, query):
        self.queries.append(query)
        return []

    async def commit(self) -> None:
        self.commit_count += 1


class FollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_followup_batch_marks_opened_tracks_and_only_sends_unopened_reminders(self) -> None:
        now = datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc)
        opened_track = followups.FollowupTrackSnapshot(
            id="track-opened",
            recipient="opened@example.com",
            subject="Already opened",
            created_at=datetime(2026, 3, 20, 18, 0, tzinfo=timezone.utc),
        )
        unopened_track = followups.FollowupTrackSnapshot(
            id="track-unopened",
            recipient="pending@example.com",
            subject="Needs follow-up",
            created_at=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
        )
        db = FakeAsyncSession()
        load_real_open_summaries = AsyncMock(
            return_value={"track-opened": object()}
        )
        to_thread = AsyncMock(return_value=True)

        with (
            patch.object(
                followups,
                "load_real_open_summaries",
                load_real_open_summaries,
            ),
            patch.object(followups.asyncio, "to_thread", to_thread),
        ):
            await followups._process_followup_batch(
                db,
                [opened_track, unopened_track],
                now,
            )

        load_real_open_summaries.assert_awaited_once_with(
            db,
            track_ids=["track-opened", "track-unopened"],
        )
        to_thread.assert_awaited_once_with(
            followups.send_followup_reminder,
            recipient="pending@example.com",
            subject="Needs follow-up",
            sent_at=unopened_track.created_at,
            days_ago=3,
            track_id="track-unopened",
        )
        self.assertEqual(2, db.commit_count)
        self.assertEqual(2, len(db.queries))

        opened_update_params = db.queries[0].compile().params
        unopened_update_params = db.queries[1].compile().params

        self.assertIn(now, opened_update_params.values())
        self.assertIn(["track-opened"], opened_update_params.values())
        self.assertIn(now, unopened_update_params.values())
        self.assertIn("track-unopened", unopened_update_params.values())


if __name__ == "__main__":
    unittest.main()
