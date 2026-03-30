"""Proxy detection for email privacy protection services."""
import ipaddress

# Apple Mail Privacy Protection and other proxy IP ranges
APPLE_IP_RANGES = [
    ipaddress.ip_network('17.0.0.0/8'),      # Apple's primary range
    ipaddress.ip_network('104.28.0.0/16'),   # Cloudflare (used by Apple)
]

# Observed Apple Mail Privacy Protection fetches can also arrive via Akamai
# with an intentionally stripped user agent such as plain "Mozilla/5.0".
APPLE_AKAMAI_IP_RANGES = [
    ipaddress.ip_network('172.224.0.0/12'),
]

GOOGLE_PROXY_RANGES = [
    ipaddress.ip_network('66.102.0.0/20'),   # Google
    ipaddress.ip_network('66.249.64.0/19'),  # Googlebot
    ipaddress.ip_network('72.14.192.0/18'),  # Google
    ipaddress.ip_network('74.125.0.0/16'),   # Google (includes image proxy)
    ipaddress.ip_network('209.85.128.0/17'), # Google
]

MICROSOFT_HOSTED_IP_RANGES = [
    ipaddress.ip_network('51.54.0.0/15'),
    ipaddress.ip_network('51.56.0.0/14'),
]


def _looks_like_generic_apple_proxy_user_agent(user_agent: str) -> bool:
    return user_agent.strip().lower() in {"", "mozilla/5.0"}


def is_microsoft_hosted_ip(ip_str: str) -> bool:
    if not ip_str:
        return False

    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    return any(ip in network for network in MICROSOFT_HOSTED_IP_RANGES)


def detect_proxy_type(ip_str: str, user_agent: str = "") -> str | None:
    """
    Detect if an IP is from a known email proxy service.

    Returns:
        'apple' - Apple Mail Privacy Protection
        'google' - Gmail image proxy
        None - Real open (not a proxy)
    """
    if not ip_str:
        return None

    try:
        ip = ipaddress.ip_address(ip_str)

        # Check Apple ranges
        for network in APPLE_IP_RANGES:
            if ip in network:
                return "apple"

        # Apple Mail Privacy Protection can traverse Akamai with a generic UA.
        if _looks_like_generic_apple_proxy_user_agent(user_agent):
            for network in APPLE_AKAMAI_IP_RANGES:
                if ip in network:
                    return "apple"

        # Check Google ranges
        for network in GOOGLE_PROXY_RANGES:
            if ip in network:
                return "google"

        # Also check user agent for proxy indicators
        if user_agent:
            ua_lower = user_agent.lower()
            if "googleimageproxy" in ua_lower or "ggpht.com" in ua_lower:
                return "google"
            if "apple" in ua_lower and "mail" in ua_lower:
                return "apple"

    except ValueError:
        pass

    return None
