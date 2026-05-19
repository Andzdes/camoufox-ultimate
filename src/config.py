"""
Centralized configuration from environment variables.

All secrets are passed via RunPod endpoint settings (Environment Variables)
and accessed through os.environ — never baked into the Docker image.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class ProxyConfig:
    """MobileProxy.space proxy credentials."""

    server: str = ""
    username: str = ""
    password: str = ""
    rotation_key: str = ""  # proxy_key for IP rotation API

    def to_camoufox_dict(self) -> dict:
        """Format proxy credentials for Camoufox's proxy= parameter."""
        if not self.server:
            return {}
        return {
            "server": self.server,
            "username": self.username,
            "password": self.password,
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.server and self.username and self.password)


@dataclass(frozen=True)
class CaptchaConfig:
    """2Captcha API configuration."""

    api_key: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class BrowserConfig:
    """Camoufox browser configuration."""

    target_os: str = "windows"
    screen_width: int = 1920
    screen_height: int = 1080

    # WebGL — with real GPU + VirtualGL, these should match the actual GPU.
    # Set after running detect_webgl.py on the RunPod GPU Pod.
    webgl_vendor: str = ""
    webgl_renderer: str = ""

    @property
    def webgl_config(self) -> Optional[Tuple[str, str]]:
        """Return webgl_config tuple if both vendor and renderer are set."""
        if self.webgl_vendor and self.webgl_renderer:
            return (self.webgl_vendor, self.webgl_renderer)
        return None


@dataclass(frozen=True)
class AppConfig:
    """Root application configuration."""

    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    captcha: CaptchaConfig = field(default_factory=CaptchaConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)


def load_config() -> AppConfig:
    """
    Load configuration from environment variables.

    Expected env vars (set in RunPod endpoint settings):
        PROXY_SERVER          — e.g. http://gate.mobileproxy.space:1234
        PROXY_USERNAME        — MobileProxy.space login
        PROXY_PASSWORD        — MobileProxy.space password
        PROXY_ROTATION_KEY    — 32-char key for IP rotation API
        TWOCAPTCHA_API_KEY    — 2Captcha API key
        TARGET_OS             — OS fingerprint: windows|macos|linux (default: windows)
        SCREEN_WIDTH          — Screen width for fingerprint (default: 1920)
        SCREEN_HEIGHT         — Screen height for fingerprint (default: 1080)
        WEBGL_VENDOR          — e.g. "Google Inc. (NVIDIA)"
        WEBGL_RENDERER        — e.g. "ANGLE (NVIDIA, NVIDIA Tesla T4/PCIe/SSE2, ...)"

    Note: Display server (Xorg :0) is managed by entrypoint.sh, not configurable here.
    """
    return AppConfig(
        proxy=ProxyConfig(
            server=os.environ.get("PROXY_SERVER", ""),
            username=os.environ.get("PROXY_USERNAME", ""),
            password=os.environ.get("PROXY_PASSWORD", ""),
            rotation_key=os.environ.get("PROXY_ROTATION_KEY", ""),
        ),
        captcha=CaptchaConfig(
            api_key=os.environ.get("TWOCAPTCHA_API_KEY", ""),
        ),
        browser=BrowserConfig(
            target_os=os.environ.get("TARGET_OS", "windows"),
            screen_width=int(os.environ.get("SCREEN_WIDTH", "1920")),
            screen_height=int(os.environ.get("SCREEN_HEIGHT", "1080")),
            webgl_vendor=os.environ.get("WEBGL_VENDOR", ""),
            webgl_renderer=os.environ.get("WEBGL_RENDERER", ""),
        ),
    )
