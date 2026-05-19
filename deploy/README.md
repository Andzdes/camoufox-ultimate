# Deployment Guide — Camoufox Ultimate on RunPod Serverless (GPU)

## How RunPod Serverless Works

```mermaid
graph TD
    subgraph YOU["Developer (local machine)"]
        CODE["Source code + Dockerfile"] -->|git push| GH["GitHub"]
    end
    
    subgraph RUNPOD["RunPod Cloud"]
        GH -->|"Auto-build Docker image"| REG["RunPod Registry"]
        REG --> EP["Serverless Endpoint"]
        EP -->|"Scale up"| W1["Worker 1 (GPU container)"]
        EP -->|"Scale up"| W2["Worker 2 (GPU container)"]
        EP -->|"Scale up"| W3["Worker N..."]
    end
    
    N8N["N8N (your server)"] -->|"POST /runsync"| EP
    EP -->|"JSON response"| N8N
```

RunPod Serverless is a **queue-based GPU container platform**. Key concepts:

| Concept | Description |
|---------|-------------|
| **Worker** | A Docker container with a GPU. Runs your `handler.py` |
| **Endpoint** | API URL that accepts jobs and routes them to workers |
| **Cold start** | Time to spin up a new worker (~5–8 sec with Xorg + GPU) |
| **Min Workers** | Minimum number of always-warm workers (0 = scale to zero) |
| **Idle Timeout** | How long a worker stays alive after finishing a job |
| **Execution Timeout** | Max time per job (default 600s, max 7 days) |

**No docker-compose, no SSH, no manual server management.** You push code to GitHub → RunPod builds the image → creates workers automatically.

---

## Overview

This project runs Camoufox (anti-detect Firefox) with **real GPU rendering** on RunPod Serverless.

**Why GPU?** Without a GPU, Canvas/WebGL renders via software (Mesa/llvmpipe). Advanced anti-bot systems like Yandex SWS detect this by analyzing rendering timings and pixel patterns. With a real GPU + VirtualGL, the browser produces genuine hardware-rendered Canvas fingerprints.

### GPU Rendering Pipeline

```mermaid
graph LR
    ES["entrypoint.sh"] -->|"1. nvidia-smi → detect BusID"| XC["Generate xorg.conf"]
    XC -->|"2. Start"| XO["Xorg :0 (headless, NVIDIA GPU)"]
    XO -->|"3. VGL_DISPLAY=:0"| VGL["VirtualGL"]
    VGL -->|"4. vglrun python3 handler.py"| H["handler.py"]
    H -->|"Camoufox (headless=False)"| FF["Firefox / Gecko"]
    FF -->|"OpenGL calls"| VGL
    VGL -->|"Redirected to real GPU"| GPU["NVIDIA GPU"]
    GPU -->|"Real hardware Canvas / WebGL"| FF
```

**Key point:** Firefox thinks it's running on a normal desktop with a monitor. Xorg provides the display, VirtualGL intercepts all OpenGL calls and routes them to the real NVIDIA GPU. The result is genuine hardware-rendered Canvas/WebGL fingerprints that are indistinguishable from a real desktop user.

---

## Architecture Decisions (DO NOT change without understanding)

> **For future developers/agents:** The following decisions were made deliberately after research and cross-verification. Each one has a specific reason. Do not "fix" these unless you have verified the underlying assumption has changed.

### 1. `headless=False` (NOT `headless=True` or `headless="virtual"`)

**Why:** `headless=True` is detectable by advanced anti-bot systems. `headless="virtual"` spawns Camoufox's own Xvfb with a **1×1 pixel resolution bug** that is a massive fingerprint red flag. We use `headless=False` because Xorg provides a real display — the browser runs in genuine headful mode inside an invisible display server.

### 2. Xorg + VirtualGL (NOT Xvfb)

**Why:** Xvfb is a **software framebuffer** — all rendering happens on the CPU via Mesa/llvmpipe. Even with a GPU available, Xvfb ignores it. Xorg with the NVIDIA driver + VirtualGL routes OpenGL calls to the actual GPU hardware. This is the entire point of the GPU stack — real Canvas fingerprints.

### 3. `from browserforge.fingerprints import Screen` (NOT `from camoufox import Screen`)

**Why:** The official Camoufox documentation at `camoufox.com/python/usage/` explicitly shows this import path. `Screen` is part of the `browserforge` library. While `from camoufox import Screen` may work as a re-export, the documented import is `browserforge.fingerprints.Screen`. Verified in consensus with Perplexity.

### 4. `webgl_config=("vendor", "renderer")` tuple (NOT `config={"webGl:renderer": ...}`)

**Why:** `webgl_config` is the documented high-level API parameter in Camoufox that validates the vendor/renderer combination against a list of supported pairs per OS. Using the raw `config={}` dict bypasses this validation and is flagged as "advanced, use with caution" in the docs. The high-level API also warns you if the combination would cause fingerprint leaks.

### 5. No FastAPI (native RunPod handler pattern)

**Why:** RunPod Serverless uses a queue-based system, not an HTTP server. `runpod.serverless.start({"handler": handler})` manages the job queue internally. Running FastAPI alongside it would either cause a port collision or be completely unreachable — RunPod Serverless does not expose arbitrary ports.

### 6. `nvidia/opengl:1.2-glvnd-runtime-ubuntu22.04` base image (NOT `python:3.11-slim`)

**Why:** This base image includes `libglvnd`, which is essential for routing OpenGL calls to the correct NVIDIA driver. Without it, OpenGL falls back to Mesa software rendering, defeating the purpose of having a GPU.

### 7. Display managed by `entrypoint.sh`, not Python

**Why:** Xorg and VirtualGL must be running **before** Python starts. The shell script detects GPU BusID dynamically (it changes between machines), generates `xorg.conf`, starts Xorg, configures VirtualGL, and then launches `handler.py` via `vglrun`. Python code only verifies the display is available — it doesn't manage it.

### 8. `vglrun` wraps the entire `handler.py` process

**Why:** VirtualGL works by intercepting OpenGL library calls via `LD_PRELOAD`. By wrapping the entire Python process with `vglrun`, every subprocess it spawns (including Firefox/Camoufox) inherits the interception. This ensures all OpenGL rendering goes through the GPU.

### 9. `NVIDIA_DRIVER_CAPABILITIES=all` (NOT just `compute`)

**Why:** By default, NVIDIA Container Toolkit only exposes compute capabilities (CUDA). For OpenGL/graphics rendering, we need `graphics` and `compat32` capabilities. Setting `all` ensures Xorg and VirtualGL can access the GPU's full feature set.

### 10. `humanize=True` in Camoufox

**Why:** Adds realistic cursor movement patterns (up to 1.5s movement duration). Combined with real GPU rendering, this makes the browser session nearly indistinguishable from a human user. Disabling this saves a few seconds per interaction but significantly increases detection risk.

### 11. IP rotation happens BEFORE Camoufox instance creation

**Why:** `geoip=True` resolves timezone, locale, and WebRTC settings from the proxy IP **at browser launch time**. If the proxy IP changes after the browser is created, these values become inconsistent (e.g., timezone says Moscow but IP says Novosibirsk). The handler enforces correct ordering: rotate IP → then create browser.

### 12. `libasound2` (NOT `libasound2t64`)

**Why:** The Dockerfile uses Ubuntu 22.04 (`nvidia/opengl` base), where the package is still named `libasound2`. The `libasound2t64` rename only applies to Debian Bookworm (12+) and Ubuntu 24.04+. If you change the base image to a newer OS version, update this package name.

---

## Critical Details

### Proxy order matters

`geoip=True` in Camoufox automatically adjusts timezone, locale, and WebRTC to match the proxy IP — but this only works correctly if the proxy is already active **before** creating the Camoufox instance. The handler enforces this: IP rotation happens first, then Camoufox launches.

### SOCKS5 and WebRTC leak risk

If MobileProxy.space provides SOCKS5 — use with caution. SOCKS5 without UDP proxying will leak the real worker IP via WebRTC. Although `geoip=True` spoofs the WebRTC IP, for maximum reliability **use HTTP proxies** or verify that SOCKS5 covers UDP.

### 2Captcha: token only, not fingerprint

2Captcha solves only the SmartCaptcha token (visual checkbox or challenge). SWS fingerprint verification (WebGL/Canvas) is **not** handled by 2Captcha — that is Camoufox + GPU's responsibility.

```
Flow: Camoufox passes SWS fingerprint check (GPU renders real Canvas)
      → if SmartCaptcha challenge appears → 2Captcha solves the token
      → token is injected into the page
```

### RunPod timeout configuration

| Timeout | Default | Recommendation |
|---------|---------|----------------|
| **Execution Timeout** | 600s (10 min) | 300s is enough for most pages. Increase for captcha-heavy scenarios |
| **Idle Timeout** | 5s | Set to 30–60s to keep workers warm between N8N requests |
| **Max: Execution** | 7 days | Only for extreme edge cases |

---

## Prerequisites

- Docker Hub or GHCR account (for pushing images)
- RunPod account with credits
- MobileProxy.space proxy subscription (HTTP proxy, not SOCKS5)
- 2Captcha account with API key
- GitHub account (for CI/CD auto-build via RunPod)

---

## Step 1: Push to GitHub

```bash
cd camoufox-ultimate
git init
git add .
git commit -m "Initial commit — Camoufox Ultimate"
git remote add origin https://github.com/YOUR_USER/camoufox-ultimate.git
git push -u origin main
```

### Option A: RunPod GitHub Integration (recommended)

RunPod can build the Docker image directly from your GitHub repo:
1. Go to RunPod Console → **Container Registry**
2. Connect your GitHub account
3. Select the `camoufox-ultimate` repository
4. RunPod will build the image automatically on each push

### Option B: Manual build + push

```bash
docker build -t your-registry/camoufox-ultimate:latest .
docker push your-registry/camoufox-ultimate:latest
```

> **Build time:** ~8–12 minutes (Camoufox binary ~400 MB + VirtualGL + Xorg).

---

## Step 2: Detect GPU WebGL Values

Run this **once** on a RunPod Pod (not Serverless) to get the real WebGL vendor/renderer for your GPU type.

1. Create a **RunPod GPU Pod** with the same GPU type you'll use for Serverless
2. SSH into the Pod
3. Install and run:
   ```bash
   pip install camoufox[geoip]
   python -m camoufox fetch
   apt-get install -y xserver-xorg-core x11-utils
   python scripts/detect_webgl.py
   ```
4. Note the `WEBGL_VENDOR` and `WEBGL_RENDERER` values
5. Terminate the Pod

---

## Step 3: Create RunPod Serverless Endpoint

1. Go to [RunPod Console → Serverless](https://www.runpod.io/console/serverless)
2. Click **New Endpoint**
3. Configure:

| Setting | Value |
|---------|-------|
| **Container Image** | From GitHub integration or `your-registry/camoufox-ultimate:latest` |
| **GPU Type** | Tesla T4 or higher (needs OpenGL support) |
| **Max Workers** | Start with 1–3 |
| **Min Workers** | 1 (keeps one warm worker to avoid cold starts) |
| **Idle Timeout** | 30–60s (keep worker alive between requests) |
| **Execution Timeout** | 300s (increase for heavy pages with captcha) |
| **GPU Count** | 1 |

> **Avoiding cold starts:** Set **Min Workers ≥ 1**. This keeps at least one worker warm, so N8N requests don't wait for Xorg + GPU initialization (~5–8 sec). You pay for idle time, but latency drops dramatically.

4. Set **Environment Variables**:

| Variable | Description | Required |
|----------|-------------|----------|
| `PROXY_SERVER` | `http://gate.mobileproxy.space:PORT` | ✅ |
| `PROXY_USERNAME` | MobileProxy.space login | ✅ |
| `PROXY_PASSWORD` | MobileProxy.space password | ✅ |
| `PROXY_ROTATION_KEY` | 32-char key from MobileProxy dashboard | ✅ |
| `TWOCAPTCHA_API_KEY` | 2Captcha API key | ✅ |
| `TARGET_OS` | `windows` (default) | ❌ |
| `WEBGL_VENDOR` | From detect_webgl.py output | ✅ (for GPU consistency) |
| `WEBGL_RENDERER` | From detect_webgl.py output | ✅ (for GPU consistency) |

5. Click **Create Endpoint** and note the `ENDPOINT_ID`

---

## Step 4: Test the Endpoint

```bash
curl -X POST "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/runsync" \
  -H "Authorization: Bearer YOUR_RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "url": "https://example.com",
      "extract_selectors": {
        "title": "h1",
        "text": "p"
      }
    }
  }'
```

### Expected Response

```json
{
  "id": "job-abc123",
  "status": "COMPLETED",
  "output": {
    "url": "https://example.com/",
    "title": "Example Domain",
    "status": "success",
    "extracted": {
      "title": "Example Domain",
      "text": "This domain is for use in illustrative examples..."
    },
    "elapsed_seconds": 8.42
  }
}
```

---

## Step 5: N8N Integration

### HTTP Request Node Configuration

| Field | Value |
|-------|-------|
| **Method** | POST |
| **URL** | `https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/runsync` |
| **Authentication** | Header Auth |
| **Header Name** | `Authorization` |
| **Header Value** | `Bearer YOUR_RUNPOD_API_KEY` |
| **Body Type** | JSON |

### Body (Expression)

```json
{
  "input": {
    "url": "{{ $json.targetUrl }}",
    "wait_for": "networkidle",
    "extract_selectors": {
      "company_name": "h1.company-name",
      "address": ".address-block",
      "phone": ".phone-number"
    },
    "rotate_ip": true
  }
}
```

### Timeout Setting

Set the N8N HTTP Request node timeout to **at least 120 seconds** to account for:
- Cold start: 5–8 seconds (Xorg + GPU init)
- Proxy rotation: 1–5 seconds
- Page load + rendering: 5–30 seconds
- Captcha solving: 10–60 seconds

---

## Input Schema Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | *required* | Target page URL |
| `wait_for` | string | `"networkidle"` | Wait condition: `"networkidle"`, `"load"`, `"domcontentloaded"`, or CSS selector |
| `wait_timeout` | int | `30000` | Wait timeout in milliseconds |
| `extract_js` | string | `null` | JavaScript expression to evaluate on page |
| `extract_selectors` | object | `{}` | Map of `{name: CSS_selector}` for text extraction |
| `captcha_sitekey` | string | `null` | Yandex SmartCaptcha sitekey for solving |
| `rotate_ip` | bool | `true` | Whether to rotate proxy IP before this request |
| `screenshot` | bool | `false` | Return base64-encoded PNG screenshot |

---

## Cold Start Optimization

| Strategy | Effect | Trade-off |
|----------|--------|-----------|
| **Min Workers = 1** | Eliminates cold start | ~$0.20–0.50/hr idle cost (GPU dependent) |
| **Min Workers = 0** | No idle cost | 5–8 sec cold start per burst |
| **Idle Timeout = 60s** | Worker stays warm between requests | Slightly higher cost |

For steady N8N workflow usage (e.g., processing a batch of URLs), **Min Workers = 1** + **Idle Timeout = 60s** is recommended.

---

## Troubleshooting

### Xorg fails to start
- Check `/tmp/xorg.log` inside the container
- Verify the GPU is available: `nvidia-smi` should show the GPU
- Ensure `NVIDIA_DRIVER_CAPABILITIES=all` is set (done in Dockerfile)

### OpenGL renderer shows "llvmpipe" instead of NVIDIA
- VirtualGL is not correctly intercepting OpenGL calls
- Check that `vglrun` is in the entrypoint command
- Verify `VGL_DISPLAY=:0` matches the Xorg display

### Cold start takes too long
- Verify `python -m camoufox fetch` runs during `docker build` (not at runtime)
- Set **Min Workers ≥ 1** to keep a warm worker

### Captcha not solving
- Confirm `TWOCAPTCHA_API_KEY` is set and has balance
- Verify the `captcha_sitekey` matches the target page
- Check 2Captcha dashboard for error logs

### Proxy not working
- Test proxy directly: `curl -x http://user:pass@gate.mobileproxy.space:PORT https://httpbin.org/ip`
- Check MobileProxy.space dashboard for proxy status
- Ensure proxy is active before Camoufox starts (handler does this automatically)

### WebRTC leaking worker IP
- Switch from SOCKS5 to HTTP proxy in MobileProxy.space dashboard
- Or verify SOCKS5 covers UDP (most mobile proxies don't)
- `geoip=True` spoofs WebRTC IP, but HTTP proxy is the safest option
