"""
Camoufox browser session manager.

Encapsulates the full lifecycle of an anti-detect browser session:
  1. Xorg + VirtualGL display (managed by entrypoint.sh, not this module)
  2. Camoufox instance creation with proxy, geoip, fingerprint config
  3. Page creation and navigation

Architecture (GPU rendering pipeline):
  Camoufox (Firefox) → vglrun → VirtualGL → Xorg → NVIDIA GPU
  Result: real GPU-rendered Canvas/WebGL — not Mesa/llvmpipe software rendering.

Design notes:
  - Xorg and VirtualGL are started by entrypoint.sh BEFORE handler.py runs.
    This module does NOT manage the display server — it's already running.
  - Each job gets a fresh Camoufox browser context (new fingerprint).
  - Proxy must be active BEFORE creating the Camoufox instance
    (geoip=True resolves timezone/locale from the proxy IP at launch).
"""

import logging
import os
from contextlib import contextmanager
from typing import Optional

from browserforge.fingerprints import Screen
from camoufox.sync_api import Camoufox

from src.config import BrowserConfig

logger = logging.getLogger(__name__)


def verify_display() -> str:
    """
    Verify that a DISPLAY environment variable is set.

    Xorg is started by entrypoint.sh before handler.py runs.
    This function only checks that the display is available.

    Returns:
        The DISPLAY value.

    Raises:
        RuntimeError: If DISPLAY is not set.
    """
    display = os.environ.get("DISPLAY")
    if not display:
        raise RuntimeError(
            "DISPLAY environment variable not set. "
            "Xorg should be started by entrypoint.sh before handler.py runs."
        )
    logger.info("Using display %s (Xorg + VirtualGL managed by entrypoint.sh)", display)
    return display


@contextmanager
def create_browser_session(
    browser_config: BrowserConfig,
    proxy_dict: Optional[dict] = None,
):
    """
    Create a Camoufox browser session as a context manager.

    Each call creates a fresh browser with a new fingerprint.
    The browser runs in headful mode — Xorg with real GPU provides the display.
    VirtualGL (vglrun) redirects OpenGL calls to the NVIDIA GPU,
    so Canvas/WebGL fingerprints are genuine hardware-rendered.

    Args:
        browser_config: Browser configuration (OS, screen, WebGL).
        proxy_dict: Formatted proxy dict for Camoufox (from ProxyConfig.to_camoufox_dict()).

    Yields:
        Camoufox browser context manager result.
    """
    # Verify display is available (Xorg should already be running)
    verify_display()

    # Build screen constraints
    screen = Screen(
        min_width=browser_config.screen_width,
        max_width=browser_config.screen_width,
        min_height=browser_config.screen_height,
        max_height=browser_config.screen_height,
    )

    # Build Camoufox kwargs
    kwargs = {
        "headless": False,  # Real headful mode — Xorg + GPU provides the display
        "screen": screen,
        "os": [browser_config.target_os],
        "geoip": True,
        "humanize": True,   # Realistic cursor movements
    }

    # Add proxy if configured
    if proxy_dict:
        kwargs["proxy"] = proxy_dict

    # Add WebGL config if configured.
    # With a real GPU + VirtualGL, the actual rendering matches the declared
    # WebGL vendor/renderer — no inconsistency for SWS to detect.
    webgl = browser_config.webgl_config
    if webgl:
        kwargs["webgl_config"] = webgl

    logger.info(
        "Creating Camoufox session (os=%s, proxy=%s, webgl=%s, display=%s)",
        browser_config.target_os,
        bool(proxy_dict),
        webgl[0] if webgl else "auto",
        os.environ.get("DISPLAY", "?"),
    )

    with Camoufox(**kwargs) as browser:
        yield browser
