import smtplib
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app import notifications


class NotificationsTests(unittest.TestCase):
    def test_send_open_notification_returns_false_when_smtp_fails(self) -> None:
        fake_settings = SimpleNamespace(
            smtp_server="smtp.example.com",
            smtp_port=587,
            smtp_username="mailer@example.com",
            smtp_password="secret",
            notification_email="alerts@example.com",
        )

        with (
            patch.object(notifications, "settings", fake_settings),
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
