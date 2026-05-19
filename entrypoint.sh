#!/bin/bash
set -e

# ═══════════════════════════════════════════════════════════════════════════════
# Camoufox Ultimate — Container Entrypoint
#
# Sequence:
#   1. Detect GPU PCI BusID via nvidia-smi
#   2. Generate xorg.conf dynamically (GPU address changes between machines)
#   3. Start headless Xorg on :0 (using real NVIDIA GPU, not software rendering)
#   4. Configure VirtualGL server
#   5. Launch handler.py through vglrun (OpenGL calls → real GPU)
# ═══════════════════════════════════════════════════════════════════════════════

echo "═══ Camoufox Ultimate — GPU Entrypoint ═══"

# ─── Step 1: Detect GPU ───────────────────────────────────────────────────────
echo "[1/5] Detecting GPU..."
nvidia-smi --query-gpu=name,driver_version,pci.bus_id --format=csv,noheader
echo ""

# Get PCI BusID and convert from 0000:XX:XX.X → PCI:XX:XX:X format for Xorg
RAW_BUS_ID=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | head -n 1 | tr -d ' ')
# Convert 0000:4A:00.0 → PCI:74:0:0 (hex to decimal)
BUS_ID=$(echo "$RAW_BUS_ID" | sed -E 's/0000:([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})\.([0-9])/\1 \2 \3/' | \
    awk '{printf "PCI:%d:%d:%d", strtonum("0x"$1), strtonum("0x"$2), $3}')

echo "   GPU BusID: $RAW_BUS_ID → $BUS_ID"

# ─── Step 2: Generate xorg.conf ───────────────────────────────────────────────
echo "[2/5] Generating xorg.conf..."
cat <<EOF > /etc/X11/xorg.conf
Section "ServerLayout"
    Identifier     "Layout0"
    Screen      0  "Screen0"
EndSection

Section "Device"
    Identifier     "Device0"
    Driver         "nvidia"
    BusID          "$BUS_ID"
    Option         "AllowEmptyInitialConfiguration" "True"
    Option         "UseDisplayDevice" "none"
EndSection

Section "Screen"
    Identifier     "Screen0"
    Device         "Device0"
    DefaultDepth    24
    SubSection     "Display"
        Depth       24
        Modes      "1920x1080"
    EndSubSection
EndSection
EOF

echo "   /etc/X11/xorg.conf written"

# ─── Step 3: Start headless Xorg ──────────────────────────────────────────────
echo "[3/5] Starting headless Xorg on :0..."

# Kill any existing X server
pkill -9 Xorg 2>/dev/null || true
sleep 0.5

# Start Xorg in background
# -noreset: don't reset after last client disconnects
# +extension GLX: enable GLX (required for VirtualGL)
Xorg -noreset +extension GLX +extension RANDR +extension RENDER \
    -logfile /tmp/xorg.log \
    -config /etc/X11/xorg.conf \
    :0 &

XORG_PID=$!
export DISPLAY=:0

# Wait for Xorg to be ready
sleep 3

if ! kill -0 $XORG_PID 2>/dev/null; then
    echo "ERROR: Xorg failed to start. Log:"
    cat /tmp/xorg.log 2>/dev/null || echo "(no log)"
    exit 1
fi

echo "   Xorg running (pid $XORG_PID)"

# Verify GPU rendering
echo "   Checking OpenGL renderer..."
RENDERER=$(vglrun glxinfo 2>/dev/null | grep "OpenGL renderer" | head -1 || echo "unknown")
echo "   $RENDERER"

# ─── Step 4: Configure VirtualGL ─────────────────────────────────────────────
echo "[4/5] Configuring VirtualGL..."

# VGL_DISPLAY tells VirtualGL which X display has the GPU
export VGL_DISPLAY=:0

# Configure VirtualGL server (non-interactive)
# This grants access to the GPU for all users in the container
/opt/VirtualGL/bin/vglserver_config -config +s +f -t 2>/dev/null || true

echo "   VGL_DISPLAY=$VGL_DISPLAY"

# ─── Step 5: Launch handler ──────────────────────────────────────────────────
echo "[5/5] Starting handler via vglrun..."
echo "═══════════════════════════════════════════"

# vglrun intercepts OpenGL calls from Firefox/Camoufox and redirects them
# to the real GPU instead of Mesa/llvmpipe software renderer.
exec vglrun python3 -u handler.py
