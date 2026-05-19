"""
2Captcha integration for Yandex SmartCaptcha solving.

Flow:
  1. Camoufox handles SWS fingerprint verification (WebGL/Canvas) automatically.
  2. If a SmartCaptcha visual challenge appears → this module solves the token.
  3. The solved token is returned for injection into the page.

Note: 2Captcha solves the CAPTCHA token only.
      SWS fingerprint verification is Camoufox's responsibility.
"""

import logging
from typing import Optional

from twocaptcha import TwoCaptcha
from twocaptcha.api import ApiException, NetworkException, TimeoutException

from src.config import CaptchaConfig

logger = logging.getLogger(__name__)

# Default timeout for captcha solving (seconds)
DEFAULT_TIMEOUT = 120


def create_solver(config: CaptchaConfig) -> Optional[TwoCaptcha]:
    """
    Create a 2Captcha solver instance.

    Args:
        config: Captcha configuration with API key.

    Returns:
        TwoCaptcha solver instance, or None if not configured.
    """
    if not config.is_configured:
        logger.warning("2Captcha API key not configured — captcha solving disabled")
        return None

    solver = TwoCaptcha(config.api_key)
    solver.pollingInterval = 5  # seconds between status checks
    return solver


def solve_yandex_smart_captcha(
    solver: TwoCaptcha,
    sitekey: str,
    page_url: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """
    Solve a Yandex SmartCaptcha challenge and return the token.

    Args:
        solver: Initialized TwoCaptcha solver.
        sitekey: SmartCaptcha sitekey from the target page (found in page source).
        page_url: URL of the page where the captcha appears.
        timeout: Maximum time to wait for solution (seconds).

    Returns:
        Solved captcha token string, or None on failure.
    """
    try:
        logger.info("Solving Yandex SmartCaptcha for %s (sitekey: %s...)", page_url, sitekey[:12])
        result = solver.yandex_smart(
            sitekey=sitekey,
            url=page_url,
        )
        token = result.get("code") if isinstance(result, dict) else str(result)
        logger.info("SmartCaptcha solved successfully (token: %s...)", token[:20] if token else "N/A")
        return token

    except TimeoutException:
        logger.error("SmartCaptcha solving timed out after %ds", timeout)
        return None
    except ApiException as exc:
        logger.error("2Captcha API error: %s", exc)
        return None
    except NetworkException as exc:
        logger.error("2Captcha network error: %s", exc)
        return None
    except Exception as exc:
        logger.error("Unexpected captcha solving error: %s", exc)
        return None


def inject_captcha_token(page, token: str) -> bool:
    """
    Inject a solved SmartCaptcha token into the page.

    Attempts multiple strategies to inject the token:
    1. Set the hidden input field value
    2. Execute the SmartCaptcha callback if available

    Args:
        page: Playwright page object.
        token: Solved captcha token.

    Returns:
        True if injection was successful.
    """
    try:
        # Strategy 1: Fill the hidden input field (Yandex SmartCaptcha standard)
        page.evaluate(
            """(token) => {
                // Try standard SmartCaptcha response field
                const input = document.querySelector(
                    'input[name="smart-token"],' +
                    'input[name="smartCaptcha-token"],' +
                    'textarea[name="smart-token"]'
                );
                if (input) {
                    input.value = token;
                    return true;
                }

                // Try setting via window callback
                if (window.smartCaptcha && typeof window.smartCaptcha.setToken === 'function') {
                    window.smartCaptcha.setToken(token);
                    return true;
                }

                // Try generic callback approach
                if (typeof window.onSmartCaptchaSuccess === 'function') {
                    window.onSmartCaptchaSuccess(token);
                    return true;
                }

                return false;
            }""",
            token,
        )
        logger.info("Captcha token injected into page")
        return True

    except Exception as exc:
        logger.error("Failed to inject captcha token: %s", exc)
        return False
