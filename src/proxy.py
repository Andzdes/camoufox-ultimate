"""
MobileProxy.space proxy management and IP rotation.

Handles:
- Formatting proxy credentials for Camoufox
- Triggering IP rotation via the MobileProxy.space API before each session
"""

import logging
from typing import Optional

import httpx

from src.config import ProxyConfig

logger = logging.getLogger(__name__)

# MobileProxy.space IP rotation endpoint (no rate limits)
ROTATION_ENDPOINT = "https://changeip.mobileproxy.space/"

# Standard User-Agent required for programmatic rotation requests
ROTATION_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def rotate_ip(config: ProxyConfig, timeout: float = 30.0) -> Optional[str]:
    """
    Request a new IP address from MobileProxy.space.

    Must be called BEFORE creating a Camoufox instance so that geoip=True
    can correctly resolve timezone/locale/WebRTC for the new IP.

    Args:
        config: Proxy configuration with rotation_key.
        timeout: HTTP request timeout in seconds.

    Returns:
        New IP address string on success, None on failure.
    """
    if not config.rotation_key:
        logger.warning("Proxy rotation key not configured — skipping IP rotation")
        return None

    try:
        response = httpx.get(
            ROTATION_ENDPOINT,
            params={
                "proxy_key": config.rotation_key,
                "format": "json",
            },
            headers={"User-Agent": ROTATION_UA},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        new_ip = data.get("new_ip") or data.get("ip") or data.get("proxy_ip")
        if new_ip:
            logger.info("IP rotated successfully → %s", new_ip)
        else:
            # Some API versions return status only
            logger.info("IP rotation request sent (response: %s)", data)

        return new_ip

    except httpx.TimeoutException:
        logger.error("IP rotation timed out after %.0fs", timeout)
        return None
    except httpx.HTTPStatusError as exc:
        logger.error(
            "IP rotation HTTP error %d: %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return None
    except Exception as exc:
        logger.error("IP rotation failed: %s", exc)
        return None
