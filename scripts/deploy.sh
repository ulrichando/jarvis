#!/bin/bash
# JARVIS Hot Deploy — apply code changes without restarting
# Usage: ./scripts/deploy.sh

JARVIS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "[JARVIS] Deploying changes..."

# Clear Python cache
find "$JARVIS_ROOT" -path "*__pycache__*" -name "*.pyc" -delete 2>/dev/null

# Hot reload via API (no restart needed)
RESULT=$(curl -s -X POST http://localhost:8765/api/reload 2>/dev/null)

if echo "$RESULT" | grep -q '"reloaded"'; then
    echo "[JARVIS] Hot reload successful"
    echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
else
    echo "[JARVIS] Hot reload failed — doing full restart..."
    pkill -f "src.server.web_server" 2>/dev/null
    sleep 2
    cd "$JARVIS_ROOT"
    bash "$JARVIS_ROOT/scripts/fix-audio.sh" 2>&1 | tail -1
    PYTHONUNBUFFERED=1 python3 -m src.server.web_server > /tmp/jarvis-web.log 2>&1 &
    sleep 10
    if curl -s http://localhost:8765/api/ready > /dev/null 2>&1; then
        echo "[JARVIS] Full restart successful"
    else
        echo "[JARVIS] Restart failed — check logs: tail /tmp/jarvis-web.log"
    fi
fi
