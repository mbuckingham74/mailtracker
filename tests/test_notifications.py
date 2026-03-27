import smtplib
import unittest
from datetime import datetime, timezone
from email import message_from_string
from email.header import decode_header
from types import SimpleNamespace
from unittest.mock import patch

from app import notifications


def decode_subject(raw_message: str) -> str:
    message = message_from_string(raw_message)
    fragments = []
    for value, encoding in decode_header(message["Subject"]):
        if isinstance(value, bytes):
            fragments.append(value.decode(encoding or "utf-8"))
        else:
            fragments.append(value)
    return "".join(fragments)


class FakeSMTP:
    def __init__(self, host, port) -> None:
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def sendmail(self, sender: str, recipient: str, message: str) -> None:
        self.sent = (sender, recipient, message)


class NotificationsTests(unittest.TestCase):
    def _build_settings(self, **overrides):
        base = {
            "smtp_server": "smtp.example.com",
            "smtp_port": 587,
            "smtp_username": "mailer@example.com",
            "smtp_password": "secret",
            "notification_email": "alerts@example.com",
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_format_time_elapsed_handles_negative_and_compound_durations(self) -> None:
        sent_at = datetime(2026, 3, 27, 17, 0, tzinfo=timezone.utc)

        self.assertEqual(
            "immediately",
            notifications.format_time_elapsed(sent_at, sent_at - notifications.datetime.resolution),
        )
        self.assertEqual(
            "1 hour, 1 minute",
            notifications.format_time_elapsed(
                sent_at,
                sent_at.replace(hour=18, minute=1, second=5),
            ),
        )
        self.assertEqual(
            "1 day, 1 hour",
            notifications.format_time_elapsed(
                sent_at,
                sent_at.replace(day=28, hour=18),
            ),
        )

    def test_is_email_notifications_enabled_requires_all_fields(self) -> None:
        with patch.object(notifications, "settings", self._build_settings()):
            self.assertTrue(notifications.is_email_notifications_enabled())

        with patch.object(notifications, "settings", self._build_settings(notification_email="")):
            self.assertFalse(notifications.is_email_notifications_enabled())

    def test_send_open_notification_returns_true_when_smtp_succeeds(self) -> None:
        fake_smtp = FakeSMTP("smtp.example.com", 587)

        with (
            patch.object(notifications, "settings", self._build_settings()),
            patch.object(notifications, "is_email_notifications_enabled", return_value=True),
            patch.object(notifications.smtplib, "SMTP", return_value=fake_smtp),
        ):
            sent = notifications.send_open_notification(
                recipient="alice@example.com",
                subject="Hello",
                opened_at=datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc),
                country="United States",
                city="New York",
                track_id="track-1",
                sent_at=datetime(2026, 3, 27, 17, 0, tzinfo=timezone.utc),
            )

        self.assertTrue(sent)
        self.assertTrue(fake_smtp.started_tls)
        self.assertEqual(("mailer@example.com", "secret"), fake_smtp.logged_in)
        self.assertIn(
            "Subject: alice read your message 1 hour after you sent it",
            fake_smtp.sent[2],
        )

    def test_send_open_notification_returns_false_when_smtp_fails(self) -> None:
        with (
            patch.object(notifications, "settings", self._build_settings()),
            patch.object(notifications, "is_email_notifications_enabled", return_value=True),
            patch.object(
                notifications.smtplib,
                "SMTP",
                side_effect=smtplib.SMTPException("boom"),
            ),
        ):
            sent = notifications.send_open_notification(
                recipient="alice@example.com",
                subject="Hello",
                opened_at=datetime(2026, 3, 27, 18, 0, tzinfo=timezone.utc),
                country="United States",
                city="New York",
                track_id="track-1",
                sent_at=datetime(2026, 3, 27, 17, 0, tzinfo=timezone.utc),
            )

        self.assertFalse(sent)

    def test_send_followup_reminder_returns_false_when_disabled(self) -> None:
        with patch.object(notifications, "is_email_notifications_enabled", return_value=False):
            sent = notifications.send_followup_reminder(
                recipient="alice@example.com",
                subject="Hello",
                sent_at=datetime(2026, 3, 27, 17, 0, tzinfo=timezone.utc),
                days_ago=3,
                track_id="track-1",
            )

        self.assertFalse(sent)

    def test_send_followup_reminder_succeeds(self) -> None:
        fake_smtp = FakeSMTP("smtp.example.com", 587)

        with (
            patch.object(notifications, "settings", self._build_settings()),
            patch.object(notifications, "is_email_notifications_enabled", return_value=True),
            patch.object(notifications.smtplib, "SMTP", return_value=fake_smtp),
        ):
            sent = notifications.send_followup_reminder(
                recipient="alice@example.com",
                subject="Follow up",
                sent_at=datetime(2026, 3, 24, 17, 0, tzinfo=timezone.utc),
                days_ago=3,
                track_id="track-2",
            )

        self.assertTrue(sent)
        self.assertIn("Subject: Follow-up Reminder: Follow up", fake_smtp.sent[2])

    def test_send_hot_conversation_notification_succeeds(self) -> None:
        fake_smtp = FakeSMTP("smtp.example.com", 587)

        with (
            patch.object(notifications, "settings", self._build_settings()),
            patch.object(notifications, "is_email_notifications_enabled", return_value=True),
            patch.object(notifications.smtplib, "SMTP", return_value=fake_smtp),
        ):
            sent = notifications.send_hot_conversation_notification(
                recipient="alice@example.com",
                subject="Hello",
                open_count=4,
                track_id="track-3",
            )

        self.assertTrue(sent)
        self.assertIn("Hot conversation", decode_subject(fake_smtp.sent[2]))

    def test_send_revived_conversation_notification_succeeds(self) -> None:
        fake_smtp = FakeSMTP("smtp.example.com", 587)

        with (
            patch.object(notifications, "settings", self._build_settings()),
            patch.object(notifications, "is_email_notifications_enabled", return_value=True),
            patch.object(notifications.smtplib, "SMTP", return_value=fake_smtp),
        ):
            sent = notifications.send_revived_conversation_notification(
                recipient="alice@example.com",
                subject="Hello",
                days_since_first_open=20,
                track_id="track-4",
            )

        self.assertTrue(sent)
        self.assertIn("Old conversation revived", decode_subject(fake_smtp.sent[2]))
