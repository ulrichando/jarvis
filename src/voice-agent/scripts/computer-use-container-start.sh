#!/usr/bin/env bash
# Entrypoint for the cloud computer-use desktop container.
# Xvfb :99 -> openbox + tint2 -> x11vnc -> websockify(0.0.0.0) -> firefox -> sidecar.
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
GEOM="${JARVIS_CU_GEOMETRY:-1280x800x24}"
VNC_PORT="${JARVIS_CU_VNC_PORT:-5900}"
WS_PORT="${JARVIS_CU_WS_PORT:-6080}"
PASS="${JARVIS_CU_VNC_PASSWORD:?JARVIS_CU_VNC_PASSWORD is required}"
RFBAUTH="/tmp/computer-use-vnc.rfbauth"

# 1. Virtual display
Xvfb "$DISPLAY" -screen 0 "$GEOM" -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
for _ in $(seq 1 50); do xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break; sleep 0.1; done

# 2. Window manager + panel (EWMH so wmctrl enumeration / focus_app works)
openbox >/tmp/openbox.log 2>&1 &
tint2 >/tmp/tint2.log 2>&1 &

# 2b. Desktop surface — pcmanfm --desktop paints the wallpaper (configured in
# the image at ~/.config/pcmanfm/default) + desktop icons + right-click app
# menu, so a fresh connect reads as a real desktop, not a black void behind one
# browser window. Best-effort; the WM works without it.
( sleep 0.5; command -v pcmanfm >/dev/null && \
  pcmanfm --desktop --profile default >/tmp/pcmanfm-desktop.log 2>&1 & ) || true

# 3. VNC server (localhost in-container) with the fixed password
x11vnc -storepasswd "$PASS" "$RFBAUTH" >/dev/null 2>&1
x11vnc -display "$DISPLAY" -rfbport "$VNC_PORT" -rfbauth "$RFBAUTH" \
  -localhost -forever -shared -noxdamage -ncache 0 -quiet -bg >/tmp/x11vnc.log 2>&1

# 4. websockify — bind 0.0.0.0 so Caddy (another container) can reach it
websockify --daemon "0.0.0.0:${WS_PORT}" "127.0.0.1:${VNC_PORT}" >/tmp/websockify.log 2>&1

# 5. Pre-launch a browser so the desktop isn't empty (best-effort)
( sleep 1; firefox --no-remote about:blank >/tmp/firefox.log 2>&1 & ) || true

# 6. The sidecar as the container's main process
cd /app
exec python computer_use_service.py
