# ═══════════════════════════════════════════════════════════════════════════════
# Camoufox Ultimate — RunPod Serverless GPU Worker
#
# Real GPU Canvas rendering via: NVIDIA Driver → Xorg (headless) → VirtualGL
# No software rendering (Mesa/llvmpipe) — SWS will see genuine GPU fingerprints.
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Base: NVIDIA OpenGL + CUDA runtime ────────────────────────────────────────
# This base image includes libglvnd, which routes OpenGL calls to the real
# NVIDIA driver (provided by the host via nvidia-container-toolkit).
# DO NOT install nvidia drivers inside the container — the host provides them.
FROM nvidia/opengl:1.2-glvnd-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# ─── System dependencies ──────────────────────────────────────────────────────
# Two groups:
#   1. Xorg + VirtualGL — real GPU-accelerated display server
#   2. Firefox/Camoufox runtime libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    # --- Python ---
    python3 \
    python3-pip \
    python3-venv \
    # --- Xorg headless (replaces Xvfb — uses real GPU) ---
    xserver-xorg-core \
    xserver-xorg-video-nvidia-535 \
    x11-xserver-utils \
    x11-utils \
    # --- VirtualGL (from official repo) ---
    wget \
    gnupg \
    ca-certificates \
    # --- Firefox/Camoufox runtime deps ---
    libgtk-3-0 \
    libasound2 \
    libx11-xcb1 \
    libdbus-glib-1-2 \
    libxt6 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxcursor1 \
    libxi6 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libnss3 \
    libnspr4 \
    fonts-liberation \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Note: Ubuntu 22.04 uses libasound2 (not libasound2t64 — that's Bookworm 12+)

# ─── Install VirtualGL from official repo ──────────────────────────────────────
RUN wget -q -O- https://packagecloud.io/dcommander/virtualgl/gpgkey \
        | gpg --dearmor > /etc/apt/trusted.gpg.d/VirtualGL.gpg \
    && echo "deb https://packagecloud.io/dcommander/virtualgl/ubuntu/ jammy main" \
        > /etc/apt/sources.list.d/VirtualGL.list \
    && apt-get update \
    && apt-get install -y virtualgl \
    && rm -rf /var/lib/apt/lists/*

# ─── Tell NVIDIA container runtime we need graphics (not just compute) ─────────
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=all

# ─── Python dependencies ──────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# CRITICAL: Download Camoufox browser binary during build (~400 MB).
# Must NOT be downloaded on every cold start.
RUN python3 -m camoufox fetch

# ─── Xorg config template (BusID is injected at runtime) ──────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ─── Application code ─────────────────────────────────────────────────────────
COPY handler.py .
COPY src/ src/

# ─── Entry point ───────────────────────────────────────────────────────────────
# entrypoint.sh:
#   1. Detects GPU BusID via nvidia-smi
#   2. Generates xorg.conf dynamically
#   3. Starts Xorg on :0
#   4. Configures VirtualGL
#   5. Launches handler.py via vglrun
ENTRYPOINT ["/entrypoint.sh"]
