import os
import unittest
from unittest.mock import patch

from app.config import load_settings


BASE_ENV = {
    "DATABASE_URL": "sqlite+aiosqlite:///test.db",
    "SECRET_KEY": "test-secret",
    "API_KEY": "test-api-key",
    "BASE_URL": "https://example.com",
    "DASHBOARD_USERNAME": "test-user",
    "DASHBOARD_PASSWORD": "test-password",
}


class ConfigTests(unittest.TestCase):
    def test_load_settings_rejects_invalid_followup_days(self) -> None:
        env = dict(BASE_ENV)
        env["FOLLOWUP_DAYS"] = "three"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Invalid FOLLOWUP_DAYS"):
                load_settings()

    def test_load_settings_rejects_invalid_smtp_port(self) -> None:
        env = dict(BASE_ENV)
        env["SMTP_PORT"] = "smtp"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Invalid SMTP_PORT"):
                load_settings()

    def test_load_settings_rejects_invalid_timezone(self) -> None:
        env = dict(BASE_ENV)
        env["DISPLAY_TIMEZONE"] = "Mars/Olympus"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Invalid DISPLAY_TIMEZONE"):
                load_settings()
