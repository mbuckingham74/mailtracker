import os


os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("BASE_URL", "https://example.com")
os.environ.setdefault("DASHBOARD_USERNAME", "test-user")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password")
