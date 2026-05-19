#!/usr/bin/env python3
"""
GPU WebGL Detection Script.

Run this ONCE on a RunPod Pod (not Serverless) with a GPU to discover
the real WebGL vendor and renderer strings for the hardware.

These values should then be set as environment variables
(WEBGL_VENDOR, WEBGL_RENDERER) in the RunPod Serverless endpoint settings
so that Camoufox's fingerprint matches the actual GPU.

Usage:
  1. Create a RunPod Pod with the same GPU type you'll use for Serverless
  2. SSH in and install deps:
       pip install camoufox[geoip]
       python -m camoufox fetch
       apt-get install -y xserver-xorg-core x11-utils
  3. Run: python detect_webgl.py
  4. Copy the output values to your Serverless endpoint env vars

Requires: camoufox[geoip], Xorg with NVIDIA driver (available on RunPod GPU Pods)
"""

import json
import os
import subprocess
import time


def main():
    # On a RunPod Pod, NVIDIA drivers are available from the host.
    # We start a headless Xorg pointing at the real GPU.

    print("[*] Detecting GPU...")
    subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,pci.bus_id", "--format=csv,noheader"],
        check=True,
    )

    # Get GPU PCI BusID
    raw_bus_id = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=pci.bus_id", "--format=csv,noheader"]
    ).decode().strip().split("\n")[0].strip()

    # Convert 0000:XX:XX.X → PCI:dec:dec:dec for Xorg
    import re
    m = re.match(r"0000:([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})\.(\d)", raw_bus_id)
    if not m:
        print(f"[!] Could not parse BusID: {raw_bus_id}")
        return

    bus_id = f"PCI:{int(m.group(1), 16)}:{int(m.group(2), 16)}:{m.group(3)}"
    print(f"[*] GPU BusID: {raw_bus_id} → {bus_id}")

    # Generate minimal xorg.conf
    xorg_conf = f"""
Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Device"
    Identifier "Device0"
    Driver "nvidia"
    BusID "{bus_id}"
    Option "AllowEmptyInitialConfiguration" "True"
    Option "UseDisplayDevice" "none"
EndSection

Section "Screen"
    Identifier "Screen0"
    Device "Device0"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1920x1080"
    EndSubSection
EndSection
"""
    with open("/tmp/detect_xorg.conf", "w") as f:
        f.write(xorg_conf)

    # Start headless Xorg on :99 (using real GPU, not software renderer)
    print("[*] Starting Xorg on :99 with NVIDIA GPU...")
    xorg_proc = subprocess.Popen(
        [
            "Xorg", "-noreset",
            "+extension", "GLX", "+extension", "RANDR", "+extension", "RENDER",
            "-logfile", "/tmp/detect_xorg.log",
            "-config", "/tmp/detect_xorg.conf",
            ":99",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    os.environ["DISPLAY"] = ":99"

    if xorg_proc.poll() is not None:
        print("[!] Xorg failed to start. Check /tmp/detect_xorg.log")
        return

    try:
        # Verify we're using GPU, not software renderer
        glxinfo_out = subprocess.check_output(
            ["glxinfo"], env={**os.environ, "DISPLAY": ":99"}
        ).decode()
        for line in glxinfo_out.split("\n"):
            if "OpenGL renderer" in line or "OpenGL vendor" in line:
                print(f"   {line.strip()}")

        from camoufox.sync_api import Camoufox

        print("[*] Launching Camoufox to detect WebGL as seen by the browser...")
        with Camoufox(headless=False) as browser:
            page = browser.new_page()
            page.goto("about:blank")

            # Extract WebGL information
            webgl_info = page.evaluate(
                """() => {
                    const canvas = document.createElement('canvas');
                    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                    if (!gl) return { error: 'WebGL not available' };

                    const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                    if (!debugInfo) return { error: 'WEBGL_debug_renderer_info not available' };

                    return {
                        vendor: gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL),
                        renderer: gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL),
                        gl_vendor: gl.getParameter(gl.VENDOR),
                        gl_renderer: gl.getParameter(gl.RENDERER),
                        gl_version: gl.getParameter(gl.VERSION),
                    };
                }"""
            )

        print()
        print("=" * 70)
        print("  WebGL Detection Results")
        print("=" * 70)
        print(json.dumps(webgl_info, indent=2))
        print()

        if "error" not in webgl_info:
            print("Set these environment variables in RunPod Serverless endpoint:")
            print()
            print(f'  WEBGL_VENDOR="{webgl_info["vendor"]}"')
            print(f'  WEBGL_RENDERER="{webgl_info["renderer"]}"')
            print()
            print("Then use in Camoufox as:")
            print(f'  webgl_config=("{webgl_info["vendor"]}", "{webgl_info["renderer"]}")')
        else:
            print(f"[!] Error: {webgl_info['error']}")

        print("=" * 70)

    finally:
        xorg_proc.terminate()
        xorg_proc.wait(timeout=5)
        print("[*] Xorg stopped")


if __name__ == "__main__":
    main()
