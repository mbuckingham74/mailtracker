"""Proxy detection for email privacy protection services."""
import ipaddress

# Apple Mail Privacy Protection and other proxy IP ranges
APPLE_IP_RANGES = [
    ipaddress.ip_network('17.0.0.0/8'),      # Apple's primary range
    ipaddress.ip_network('104.28.0.0/16'),   # Cloudflare (used by Apple)
]

GOOGLE_PROXY_RANGES = [
    ipaddress.ip_network('66.102.0.0/20'),   # Google
    ipaddress.ip_network('66.249.64.0/19'),  # Googlebot
    ipaddress.ip_network('72.14.192.0/18'),  # Google
    ipaddress.ip_network('74.125.0.0/16'),   # Google (includes image proxy)
    ipaddress.ip_network('209.85.128.0/17'), # Google
]


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
