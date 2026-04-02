#!/bin/bash
# JARVIS OS — Wait for web server and launch Chromium kiosk
# Called from Sway config on startup

MAX_WAIT=30
echo "[JARVIS] Waiting for web server..."

for i in $(seq 1 $MAX_WAIT); do
    if curl -s http://localhost:8765 > /dev/null 2>&1; then
        echo "[JARVIS] Web server ready after ${i}s"
        exec chromium \
            --kiosk \
            --no-first-run \
            --disable-translate \
            --disable-infobars \
            --disable-suggestions-service \
            --no-default-browser-check \
            --autoplay-policy=no-user-gesture-required \
            --use-fake-ui-for-media-stream \
            --enable-features=UseOzonePlatform \
            --ozone-platform=wayland \
            http://localhost:8765
    fi
    sleep 1
done

echo "[JARVIS] Web server not available after ${MAX_WAIT}s — opening terminal"
exec foot -e bash -c 'echo "JARVIS web server failed to start. Check: systemctl status jarvis-web"; bash'
