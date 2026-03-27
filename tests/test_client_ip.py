import ipaddress
import unittest
from unittest.mock import patch

from fastapi import Request

from app import client_ip


def build_request(*, peer_ip: str, headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": (peer_ip, 12345),
            "server": ("testserver", 443),
        }
    )


class ClientIpTests(unittest.TestCase):
    def test_parse_networks_rejects_invalid_cidr(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid TRUSTED_PROXY_CIDRS entry 'nope'"):
            client_ip._parse_networks("nope")

    def test_get_client_ip_prefers_forwarded_client_when_peer_is_trusted(self) -> None:
        request = build_request(
            peer_ip="10.0.0.2",
            headers=[(b"x-forwarded-for", b"198.51.100.10, 10.0.0.2")],
        )

        with patch.object(
            client_ip,
            "TRUSTED_PROXY_NETWORKS",
            [ipaddress.ip_network("10.0.0.0/8")],
        ):
            resolved_ip = client_ip.get_client_ip(request)

        self.assertEqual("198.51.100.10", resolved_ip)

    def test_get_client_ip_returns_peer_ip_when_proxy_headers_are_untrusted(self) -> None:
        request = build_request(
            peer_ip="198.51.100.20",
            headers=[(b"x-forwarded-for", b"203.0.113.5")],
        )

        with patch.object(
            client_ip,
            "TRUSTED_PROXY_NETWORKS",
            [ipaddress.ip_network("10.0.0.0/8")],
        ):
            resolved_ip = client_ip.get_client_ip(request)

        self.assertEqual("198.51.100.20", resolved_ip)
