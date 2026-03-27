import os
import unittest

from fastapi import Request

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("BASE_URL", "https://example.com")
os.environ.setdefault("DASHBOARD_USERNAME", "test-user")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password")

from app.web import render_template


class RenderTemplateTests(unittest.TestCase):
    def test_render_template_uses_request_first_signature(self) -> None:
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/login",
                "raw_path": b"/login",
                "query_string": b"",
                "root_path": "",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }
        )

        response = render_template(request, "login.html", {"error": None})

        self.assertEqual("login.html", response.template.name)
        self.assertIs(request, response.context["request"])
        self.assertIsNone(response.context["error"])
