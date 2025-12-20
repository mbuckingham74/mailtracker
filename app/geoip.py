import os
import gzip
import tarfile
import httpx
from pathlib import Path
from typing import Optional, Tuple
import geoip2.database
import geoip2.errors

GEOIP_DIR = Path("/app/data/geoip")
DB_PATH = GEOIP_DIR / "GeoLite2-City.mmdb"
MAXMIND_LICENSE_KEY = os.getenv("MAXMIND_LICENSE_KEY", "")

# Global reader instance
_reader: Optional[geoip2.database.Reader] = None


def get_download_url() -> str:
    """Get MaxMind download URL for GeoLite2-City database."""
    return (
        f"https://download.maxmind.com/app/geoip_download?"
        f"edition_id=GeoLite2-City&license_key={MAXMIND_LICENSE_KEY}&suffix=tar.gz"
    )


async def download_database() -> bool:
    """Download and extract the GeoLite2-City database."""
    if not MAXMIND_LICENSE_KEY:
        print("MAXMIND_LICENSE_KEY not set, skipping GeoIP database download")
        return False

    try:
        GEOIP_DIR.mkdir(parents=True, exist_ok=True)

        print("Downloading GeoLite2-City database...")
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(get_download_url())
            response.raise_for_status()

        # Save the tar.gz file
        tar_path = GEOIP_DIR / "GeoLite2-City.tar.gz"
        tar_path.write_bytes(response.content)

        # Extract the .mmdb file
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".mmdb"):
                    # Extract just the mmdb file
                    member.name = Path(member.name).name
                    tar.extract(member, GEOIP_DIR)
                    break

        # Clean up tar file
        tar_path.unlink()

        print(f"GeoLite2-City database downloaded to {DB_PATH}")
        return True

    except Exception as e:
        print(f"Failed to download GeoIP database: {e}")
        return False


def get_reader() -> Optional[geoip2.database.Reader]:
    """Get or create the GeoIP database reader."""
    global _reader

    if _reader is not None:
        return _reader

    if not DB_PATH.exists():
        return None

    try:
        _reader = geoip2.database.Reader(str(DB_PATH))
        return _reader
    except Exception as e:
        print(f"Failed to open GeoIP database: {e}")
        return None


def lookup_ip(ip_address: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Look up country and city for an IP address.
    Returns (country, city) tuple, with None for unknown values.
    """
    reader = get_reader()
    if reader is None:
        return None, None

    # Skip private/local IPs
    if ip_address.startswith(("127.", "10.", "192.168.", "172.16.", "172.17.",
                               "172.18.", "172.19.", "172.20.", "172.21.",
                               "172.22.", "172.23.", "172.24.", "172.25.",
                               "172.26.", "172.27.", "172.28.", "172.29.",
                               "172.30.", "172.31.", "::1", "fc", "fd")):
        return None, None

    try:
        response = reader.city(ip_address)
        country = response.country.name
        city = response.city.name
        return country, city
    except geoip2.errors.AddressNotFoundError:
        return None, None
    except Exception as e:
        print(f"GeoIP lookup error for {ip_address}: {e}")
        return None, None


async def init_geoip():
    """Initialize GeoIP database - download if not present."""
    if not DB_PATH.exists():
        await download_database()

    # Try to open the reader
    reader = get_reader()
    if reader:
        print("GeoIP database ready")
    else:
        print("GeoIP database not available")
