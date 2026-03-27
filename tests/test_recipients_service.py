import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.services import recipients
from app.services.open_activity import TrackRealOpenSummary
from app.services.recipients import RecipientTrackSnapshot


class FakeResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class FakeAsyncSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return FakeResult(self.rows)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 27, 18, 0, tzinfo=timezone.utc)


class RecipientsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_recipients_context_uses_default_sorting_and_pagination_bounds(self) -> None:
        recipient_list = [
            {"email": "bob@example.com", "email_lower": "bob@example.com", "sent": 3, "opened": 1, "open_rate": 33.3, "last_open": None, "last_open_display": "Never", "score": 10, "score_label": "Unengaged"},
            {"email": "alice@example.com", "email_lower": "alice@example.com", "sent": 2, "opened": 2, "open_rate": 100.0, "last_open": datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc), "last_open_display": "6 hrs ago", "score": 95, "score_label": "Highly Engaged"},
        ]

        with (
            patch.object(recipients, "datetime", FrozenDateTime),
            patch.object(recipients, "_load_recipient_list", AsyncMock(return_value=recipient_list)),
        ):
            context = await recipients.build_recipients_context(
                object(),
                search="",
                sort="invalid",
                order="invalid",
                page=99,
            )

        self.assertEqual(["alice@example.com", "bob@example.com"], [item["email"] for item in context["recipients"]])
        self.assertEqual("score", context["sort"])
        self.assertEqual("desc", context["order"])
        self.assertEqual(1, context["page"])
        self.assertEqual(2, context["total_items"])

    async def test_build_recipient_detail_context_aggregates_history_and_scores(self) -> None:
        db = FakeAsyncSession(
            [
                ("track-1", "Alice@example.com, Bob@example.com", "First", datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)),
                ("track-2", "Alice@example.com", "Second", datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)),
                ("track-3", "Carol@example.com", "Other", datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)),
            ]
        )
        summaries = {
            "track-1": TrackRealOpenSummary(
                count=2,
                first_open_at=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
                last_open_at=datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc),
            ),
            "track-2": TrackRealOpenSummary(
                count=1,
                first_open_at=datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc),
                last_open_at=datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc),
            ),
        }

        with (
            patch.object(recipients, "datetime", FrozenDateTime),
            patch.object(recipients, "load_real_open_summaries", AsyncMock(return_value=summaries)),
        ):
            context = await recipients.build_recipient_detail_context(db, "alice@example.com")

        self.assertEqual("Alice@example.com", context["email"])
        self.assertEqual(2, context["sent"])
        self.assertEqual(2, context["opened"])
        self.assertEqual(100.0, context["open_rate"])
        self.assertEqual("1.0 days", context["avg_time_to_open"])
        self.assertEqual("Highly Engaged", context["score_label"])
        self.assertEqual(2, len(context["email_history"]))
        self.assertEqual(2, context["email_history"][0]["open_count"])
        self.assertEqual(1, context["email_history"][1]["open_count"])

    async def test_build_recipient_detail_context_raises_404_for_missing_recipient(self) -> None:
        db = FakeAsyncSession([])

        with self.assertRaises(HTTPException) as exc:
            await recipients.build_recipient_detail_context(db, "missing@example.com")

        self.assertEqual(404, exc.exception.status_code)

    async def test_load_recipient_list_batches_tracks_before_finalizing(self) -> None:
        db = FakeAsyncSession(
            [
                ("track-1", "alice@example.com"),
                ("track-2", "bob@example.com"),
                ("track-3", "carol@example.com"),
            ]
        )
        accumulate = AsyncMock(
            side_effect=lambda _db, _batch, recipient_map: recipient_map.update(
                {"alice@example.com": {"display_email": "alice@example.com", "opened": 1, "sent": 1, "last_open": None}}
            )
        )
        finalize = patch.object(
            recipients,
            "_finalize_recipient_list",
            return_value=[{"email": "alice@example.com"}],
        )

        with (
            patch.object(recipients, "RECIPIENT_SUMMARY_BATCH_SIZE", 2),
            patch.object(recipients, "_accumulate_recipient_batch", accumulate),
            finalize as finalize_recipient_list,
        ):
            recipient_list = await recipients._load_recipient_list(
                db,
                search="alice",
                now=FrozenDateTime.now(timezone.utc),
            )

        self.assertEqual([{"email": "alice@example.com"}], recipient_list)
        self.assertEqual(2, accumulate.await_count)
        self.assertIn("%alice%", db.queries[0].compile().params.values())
        finalize_recipient_list.assert_called_once()

    async def test_accumulate_recipient_batch_tracks_multiple_recipients_and_last_open(self) -> None:
        recipient_map = {}
        summaries = {
            "track-1": TrackRealOpenSummary(
                count=1,
                first_open_at=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
                last_open_at=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
            ),
        }
        tracks = [
            RecipientTrackSnapshot(id="track-1", recipient="Alice@example.com, Bob@example.com"),
            RecipientTrackSnapshot(id="track-2", recipient="bob@example.com"),
        ]

        with patch.object(recipients, "load_real_open_summaries", AsyncMock(return_value=summaries)):
            await recipients._accumulate_recipient_batch(object(), tracks, recipient_map)

        self.assertEqual(1, recipient_map["alice@example.com"]["sent"])
        self.assertEqual(1, recipient_map["alice@example.com"]["opened"])
        self.assertEqual(2, recipient_map["bob@example.com"]["sent"])
        self.assertEqual(1, recipient_map["bob@example.com"]["opened"])

    def test_finalize_recipient_list_and_helpers_compute_expected_labels(self) -> None:
        now = datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc)
        recipient_list = recipients._finalize_recipient_list(
            {
                "alice@example.com": {
                    "display_email": "Alice@example.com",
                    "sent": 4,
                    "opened": 4,
                    "last_open": datetime(2026, 3, 26, 18, 0, tzinfo=timezone.utc),
                }
            },
            now,
        )

        self.assertEqual("Alice@example.com", recipient_list[0]["email"])
        self.assertEqual("Highly Engaged", recipient_list[0]["score_label"])
        self.assertEqual([("Bob@example.com", "bob@example.com")], recipients._split_recipient_emails("Bob@example.com"))
        self.assertEqual("Bob@example.com", recipients._match_recipient_email("Bob@example.com", "bob@example.com"))
        self.assertGreater(recipients._calculate_engagement_score(4, 4, now, now), 80)
        self.assertEqual("Low", recipients._get_engagement_label(25))
