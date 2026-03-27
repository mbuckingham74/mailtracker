import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.routes import dashboard as dashboard_routes


class RoutesDashboardTests(unittest.TestCase):
    def _build_client(self) -> TestClient:
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key="test-secret",
            https_only=False,
            same_site="lax",
        )
        app.include_router(dashboard_routes.router)

        async def override_db():
            return object()

        app.dependency_overrides[dashboard_routes.get_db] = override_db
        return TestClient(app, base_url="https://testserver")

    def test_dashboard_redirects_when_not_authenticated(self) -> None:
        client = self._build_client()

        response = client.get("/", follow_redirects=False)

        self.assertEqual(303, response.status_code)
        self.assertEqual("/login", response.headers["location"])

    def test_login_sets_session_and_allows_dashboard_access(self) -> None:
        client = self._build_client()
        build_dashboard_context = AsyncMock(
            return_value={
                "tracks": [],
                "filter": "all",
                "search": "",
                "date_range": "all",
                "page": 1,
                "total_pages": 1,
                "total_items": 0,
                "query_params": {},
            }
        )

        def fake_render_template(_request, name, _context):
            return HTMLResponse(name)

        with (
            patch.object(dashboard_routes, "build_dashboard_context", build_dashboard_context),
            patch.object(dashboard_routes, "render_template", side_effect=fake_render_template),
        ):
            login_response = client.post(
                "/login",
                data={"username": "test-user", "password": "test-password"},
                follow_redirects=False,
            )
            dashboard_response = client.get("/")

        self.assertEqual(303, login_response.status_code)
        self.assertEqual("/", login_response.headers["location"])
        self.assertEqual(200, dashboard_response.status_code)
        self.assertEqual("dashboard.html", dashboard_response.text)
        build_dashboard_context.assert_awaited_once()

    def test_login_with_invalid_credentials_renders_login_page(self) -> None:
        client = self._build_client()

        def fake_render_template(_request, name, context):
            return HTMLResponse(f"{name}:{context.get('error')}")

        with patch.object(dashboard_routes, "render_template", side_effect=fake_render_template):
            response = client.post(
                "/login",
                data={"username": "wrong", "password": "nope"},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual("login.html:Invalid credentials", response.text)
