import ipaddress
import os
from typing import Iterable

from fastapi import Request

DEFAULT_TRUSTED_PROXY_CIDRS = (
    "127.0.0.1/32,"
    "::1/128,"
    "10.0.0.0/8,"
    "172.16.0.0/12,"
    "192.168.0.0/16,"
    "fc00::/7"
)


def _parse_networks(raw_cidrs: str) -> list:
    networks = []
    for cidr in raw_cidrs.split(","):
        cidr = cidr.strip()
        if not cidr:
            continue
        networks.append(ipaddress.ip_network(cidr, strict=False))
    return networks


TRUSTED_PROXY_NETWORKS = _parse_networks(
    os.getenv("TRUSTED_PROXY_CIDRS", DEFAULT_TRUSTED_PROXY_CIDRS)
)


def _parse_ip(candidate: str | None):
    if not candidate:
        return None
    try:
        return ipaddress.ip_address(candidate.strip())
    except ValueError:
        return None


def _is_trusted_proxy(candidate: str | None) -> bool:
    ip = _parse_ip(candidate)
    if ip is None:
        return False
    return any(ip in network for network in TRUSTED_PROXY_NETWORKS)


def _iter_forwarded_chain(x_forwarded_for: str, peer_ip: str) -> Iterable[str]:
    for candidate in x_forwarded_for.split(","):
        candidate = candidate.strip()
        if candidate:
            yield candidate
    yield peer_ip


def get_client_ip(request: Request) -> str | None:
    """Resolve the client IP using trusted proxy headers only."""
    peer_ip = request.client.host if request.client else None
    if not peer_ip:
        return None

    if not _is_trusted_proxy(peer_ip):
        return peer_ip

    x_forwarded_for = request.headers.get("X-Forwarded-For", "")
    chain = list(_iter_forwarded_chain(x_forwarded_for, peer_ip))

    for candidate in reversed(chain):
        if not _parse_ip(candidate):
            continue
        if _is_trusted_proxy(candidate):
            continue
        return candidate

    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip and _parse_ip(x_real_ip) and not _is_trusted_proxy(x_real_ip):
        return x_real_ip.strip()

    return peer_ip
