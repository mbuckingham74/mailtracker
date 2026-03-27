import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import geoip2.errors

from app import geoip


def build_tar_bytes(member_name: str, content: bytes = b"mmdb-data") -> bytes:
    fileobj = io.BytesIO()
    with tarfile.open(fileobj=fileobj, mode="w:gz") as tar:
        info = tarfile.TarInfo(member_name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return fileobj.getvalue()


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.status_checked = False

    def raise_for_status(self) -> None:
        self.status_checked = True


class FakeAsyncClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requested_urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        self.requested_urls.append(url)
        return self.response


class GeoIpTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_database_returns_false_without_license_key(self) -> None:
        with patch.object(geoip, "settings", SimpleNamespace(maxmind_license_key="")):
            downloaded = await geoip.download_database()

        self.assertFalse(downloaded)

    async def test_download_database_extracts_mmdb_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            geoip_dir = Path(tmpdir)
            db_path = geoip_dir / "GeoLite2-City.mmdb"
            response = FakeResponse(
                build_tar_bytes("GeoLite2-City_20260327/GeoLite2-City.mmdb")
            )
            fake_client = FakeAsyncClient(response)

            with (
                patch.object(geoip, "settings", SimpleNamespace(maxmind_license_key="license-key")),
                patch.object(geoip, "GEOIP_DIR", geoip_dir),
                patch.object(geoip, "DB_PATH", db_path),
                patch.object(geoip.httpx, "AsyncClient", return_value=fake_client),
            ):
                downloaded = await geoip.download_database()
                extracted_bytes = db_path.read_bytes()

        self.assertTrue(downloaded)
        self.assertEqual(
            [
                "https://download.maxmind.com/app/geoip_download?"
                "edition_id=GeoLite2-City&license_key=license-key&suffix=tar.gz"
            ],
            fake_client.requested_urls,
        )
        self.assertTrue(response.status_checked)
        self.assertEqual(b"mmdb-data", extracted_bytes)

    async def test_download_database_returns_false_when_archive_has_no_mmdb(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            geoip_dir = Path(tmpdir)
            db_path = geoip_dir / "GeoLite2-City.mmdb"
            response = FakeResponse(build_tar_bytes("README.txt", b"no database"))
            fake_client = FakeAsyncClient(response)

            with (
                patch.object(geoip, "settings", SimpleNamespace(maxmind_license_key="license-key")),
                patch.object(geoip, "GEOIP_DIR", geoip_dir),
                patch.object(geoip, "DB_PATH", db_path),
                patch.object(geoip.httpx, "AsyncClient", return_value=fake_client),
            ):
                downloaded = await geoip.download_database()

        self.assertFalse(downloaded)
        self.assertFalse(db_path.exists())

    def test_get_reader_caches_successful_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "GeoLite2-City.mmdb"
            db_path.write_bytes(b"db")
            fake_reader = object()

            with (
                patch.object(geoip, "DB_PATH", db_path),
                patch.object(geoip, "_reader", None),
                patch.object(geoip.geoip2.database, "Reader", return_value=fake_reader) as reader_cls,
            ):
                first_reader = geoip.get_reader()
                second_reader = geoip.get_reader()

        self.assertIs(fake_reader, first_reader)
        self.assertIs(fake_reader, second_reader)
        reader_cls.assert_called_once_with(str(db_path))

    def test_lookup_ip_handles_private_not_found_and_successful_results(self) -> None:
        class FakeReader:
            def __init__(self) -> None:
                self.calls = []

            def city(self, ip_address: str):
                self.calls.append(ip_address)
                if ip_address == "8.8.4.4":
                    return SimpleNamespace(
                        country=SimpleNamespace(name="United States"),
                        city=SimpleNamespace(name="Mountain View"),
                    )
                if ip_address == "1.1.1.1":
                    raise geoip2.errors.AddressNotFoundError("missing")
                raise RuntimeError("broken reader")

        reader = FakeReader()

        with patch.object(geoip, "get_reader", return_value=reader):
            self.assertEqual((None, None), geoip.lookup_ip("10.0.0.1"))
            self.assertEqual((None, None), geoip.lookup_ip("1.1.1.1"))
            self.assertEqual(("United States", "Mountain View"), geoip.lookup_ip("8.8.4.4"))
            self.assertEqual((None, None), geoip.lookup_ip("8.8.8.8"))

        self.assertEqual(["1.1.1.1", "8.8.4.4", "8.8.8.8"], reader.calls)

    async def test_init_geoip_downloads_when_missing_and_reports_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_db_path = Path(tmpdir) / "missing.mmdb"

            with (
                patch.object(geoip, "DB_PATH", missing_db_path),
                patch.object(geoip, "download_database", AsyncMock()) as download_database,
                patch.object(geoip, "get_reader", return_value=object()),
                patch("builtins.print") as fake_print,
            ):
                await geoip.init_geoip()

        download_database.assert_awaited_once()
        fake_print.assert_any_call("GeoIP database ready")

    async def test_init_geoip_reports_when_database_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "GeoLite2-City.mmdb"
            db_path.write_bytes(b"db")

            with (
                patch.object(geoip, "DB_PATH", db_path),
                patch.object(geoip, "download_database", AsyncMock()) as download_database,
                patch.object(geoip, "get_reader", return_value=None),
                patch("builtins.print") as fake_print,
            ):
                await geoip.init_geoip()

        download_database.assert_not_awaited()
        fake_print.assert_any_call("GeoIP database not available")
