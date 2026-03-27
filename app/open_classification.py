from .proxy_detection import detect_proxy_type


def classify_open(
    ip_address: str | None,
    user_agent: str | None = None,
) -> tuple[bool, str | None]:
    proxy_type = detect_proxy_type(ip_address or "", user_agent or "")
    return proxy_type is None, proxy_type


def resolve_missing_open_classification(
    *,
    proxy_type: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[bool, str | None]:
    """Resolve legacy rows that predate persisted `is_real_open`."""
    if proxy_type is not None:
        return False, proxy_type

    return classify_open(ip_address, user_agent)
