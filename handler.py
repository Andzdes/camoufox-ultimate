"""
RunPod Serverless handler — Camoufox anti-detect browser parser.

Entry point for the RunPod worker. No FastAPI — uses the native
RunPod queue-based handler pattern.

Architecture:
  N8N → POST /runsync → RunPod Queue → handler(job) → JSON response

GPU rendering pipeline (managed by entrypoint.sh):
  entrypoint.sh → Xorg (headless, real GPU) → VirtualGL → vglrun handler.py
  Result: Firefox/Camoufox renders Canvas/WebGL on the real NVIDIA GPU.
  SWS sees genuine hardware fingerprints, not Mesa/llvmpipe.

Cold start sequence (entrypoint.sh + this file):
  1. [entrypoint.sh] Detect GPU BusID → generate xorg.conf → start Xorg
  2. [entrypoint.sh] Configure VirtualGL → launch handler.py via vglrun
  3. [handler.py]    Load config from environment variables
  4. [handler.py]    Initialize 2Captcha solver
  5. [handler.py]    Ready to accept jobs

Per-job sequence:
  1. Validate input
  2. Rotate proxy IP (if configured)
  3. Launch fresh Camoufox session (real GPU rendering via VirtualGL)
  4. Navigate to target URL
  5. Handle captcha if encountered
  6. Extract data (via JS evaluation or CSS selectors)
  7. Return JSON result
"""

import logging
import time
from typing import Any

import runpod

from src.browser import create_browser_session, verify_display
from src.captcha import create_solver, inject_captcha_token, solve_yandex_smart_captcha
from src.config import load_config
from src.proxy import rotate_ip

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("handler")

# ─── Cold start initialization (runs ONCE per worker lifecycle) ────────────────

logger.info("═══ Handler initialization ═══")

# 1. Load configuration from environment
config = load_config()
logger.info(
    "Config loaded: proxy=%s, captcha=%s, os=%s, webgl=%s",
    config.proxy.is_configured,
    config.captcha.is_configured,
    config.browser.target_os,
    bool(config.browser.webgl_config),
)

# 2. Verify display is available (Xorg started by entrypoint.sh)
verify_display()

# 3. Initialize captcha solver
captcha_solver = create_solver(config.captcha)

logger.info("═══ Handler ready — awaiting jobs ═══")


# ─── Job handler ───────────────────────────────────────────────────────────────


def handler(job: dict) -> Any:
    """
    Process a single RunPod job.

    Expected input schema:
    {
        "url": "https://target-site.com/page",            # Required
        "wait_for": "selector or networkidle",             # Optional: wait condition
        "wait_timeout": 30000,                             # Optional: wait timeout ms
        "extract_js": "() => document.title",              # Optional: JS to evaluate
        "extract_selectors": {                             # Optional: CSS selectors → data
            "title": "h1",
            "price": ".price-value",
            "description": "#description"
        },
        "captcha_sitekey": "abc123...",                    # Optional: SmartCaptcha sitekey
        "rotate_ip": true,                                 # Optional: force IP rotation
        "screenshot": false                                # Optional: return base64 screenshot
    }

    Returns:
        Dict with extracted data, or error information.
    """
    job_input = job.get("input", {})

    # ── Input validation ───────────────────────────────────────────────────
    url = job_input.get("url")
    if not url:
        return {"error": "Missing required field: 'url'"}

    wait_for = job_input.get("wait_for", "networkidle")
    wait_timeout = job_input.get("wait_timeout", 30000)
    extract_js = job_input.get("extract_js")
    extract_selectors = job_input.get("extract_selectors", {})
    captcha_sitekey = job_input.get("captcha_sitekey")
    should_rotate_ip = job_input.get("rotate_ip", True)
    should_screenshot = job_input.get("screenshot", False)

    start_time = time.time()
    logger.info("Job started: %s", url)

    try:
        # ── Step 1: Rotate proxy IP ────────────────────────────────────────
        if should_rotate_ip and config.proxy.is_configured:
            new_ip = rotate_ip(config.proxy)
            if new_ip:
                logger.info("Proxy IP rotated to %s", new_ip)

        # ── Step 2: Launch browser session ─────────────────────────────────
        proxy_dict = config.proxy.to_camoufox_dict() if config.proxy.is_configured else None

        with create_browser_session(config.browser, proxy_dict) as browser:
            page = browser.new_page()

            # ── Step 3: Navigate to target ─────────────────────────────────
            logger.info("Navigating to %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=wait_timeout)

            # Wait for desired page state
            if wait_for == "networkidle":
                page.wait_for_load_state("networkidle", timeout=wait_timeout)
            elif wait_for.startswith(".") or wait_for.startswith("#") or wait_for.startswith("["):
                # It's a CSS selector — wait for element
                page.wait_for_selector(wait_for, timeout=wait_timeout)
            else:
                page.wait_for_load_state(wait_for, timeout=wait_timeout)

            # ── Step 4: Handle captcha if needed ───────────────────────────
            if captcha_sitekey and captcha_solver:
                logger.info("Attempting to solve SmartCaptcha...")
                token = solve_yandex_smart_captcha(
                    solver=captcha_solver,
                    sitekey=captcha_sitekey,
                    page_url=url,
                )
                if token:
                    inject_captcha_token(page, token)
                    # Wait for page to process the token
                    page.wait_for_timeout(3000)
                else:
                    logger.warning("Captcha solving failed — continuing without token")

            # ── Step 5: Extract data ───────────────────────────────────────
            result = {
                "url": page.url,
                "title": page.title(),
                "status": "success",
            }

            # Execute custom JS extraction
            if extract_js:
                try:
                    result["js_result"] = page.evaluate(extract_js)
                except Exception as exc:
                    result["js_error"] = str(exc)

            # Extract via CSS selectors
            if extract_selectors:
                extracted = {}
                for key, selector in extract_selectors.items():
                    try:
                        element = page.query_selector(selector)
                        extracted[key] = element.inner_text() if element else None
                    except Exception as exc:
                        extracted[key] = f"error: {exc}"
                result["extracted"] = extracted

            # Take screenshot if requested
            if should_screenshot:
                screenshot_bytes = page.screenshot(type="png")
                import base64
                result["screenshot_base64"] = base64.b64encode(screenshot_bytes).decode()

            # ── Step 6: Return results ─────────────────────────────────────
            elapsed = time.time() - start_time
            result["elapsed_seconds"] = round(elapsed, 2)
            logger.info("Job completed in %.2fs", elapsed)
            return result

    except Exception as exc:
        elapsed = time.time() - start_time
        logger.error("Job failed after %.2fs: %s", elapsed, exc, exc_info=True)
        return {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "elapsed_seconds": round(elapsed, 2),
            "status": "failed",
        }


# ─── RunPod entry point ───────────────────────────────────────────────────────

runpod.serverless.start({"handler": handler})
