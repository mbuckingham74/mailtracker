from dataclasses import dataclass
from datetime import datetime

from .proxy_detection import detect_proxy_type


@dataclass(frozen=True)
class ResolvedOpenSnapshot:
    opened_at: datetime | None
    ip_address: str | None
    user_agent: str | None
    country: str | None
    city: str | None
    proxy_type: str | None
    is_real_open: bool


def classify_open(
    ip_address: str | None,
    user_agent: str | None = None,
) -> tuple[bool, str | None]:
    proxy_type = detect_proxy_type(ip_address or "", user_agent or "")
    return proxy_type is None, proxy_type


def resolve_open_classification(
    *,
    is_real_open: bool | None,
    proxy_type: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[bool, str | None]:
    if is_real_open is not None:
        if bool(is_real_open):
            return True, None

        if proxy_type is not None:
            return False, proxy_type

        _, resolved_proxy_type = classify_open(ip_address, user_agent)
        return False, resolved_proxy_type

    if proxy_type is not None:
        return False, proxy_type

    return classify_open(ip_address, user_agent)


def resolve_open_snapshot(
    *,
    opened_at: datetime | None,
    is_real_open: bool | None,
    proxy_type: str | None,
    ip_address: str | None,
    user_agent: str | None,
    country: str | None = None,
    city: str | None = None,
) -> ResolvedOpenSnapshot:
    resolved_is_real_open, resolved_proxy_type = resolve_open_classification(
        is_real_open=is_real_open,
        proxy_type=proxy_type,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return ResolvedOpenSnapshot(
        opened_at=opened_at,
        ip_address=ip_address,
        user_agent=user_agent,
        country=country,
        city=city,
        proxy_type=resolved_proxy_type,
        is_real_open=resolved_is_real_open,
    )
