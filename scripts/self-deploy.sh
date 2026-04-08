#!/bin/bash
# JARVIS self-deploy — called after self-modification to reload changes
# Usage: self-deploy.sh [--python|--frontend|--server|--extension]
set -e
JARVIS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$JARVIS_ROOT"

MODE="${1:---python}"

case "$MODE" in
  --python)
    # Editable install is already live — signal server to reload changed modules
    if [ -f /tmp/jarvis-server.pid ]; then
      kill -HUP "$(cat /tmp/jarvis-server.pid)" 2>/dev/null || true
    fi
    echo "Python modules reloaded (editable install — changes are live)"
    ;;
  --frontend)
    cd src/server/frontend
    npm run build
    echo "Frontend rebuilt and ready"
    ;;
  --server)
    # Full server restart (use when adding new routes or changing startup code)
    if [ -f /tmp/jarvis-server.pid ]; then
      kill -9 "$(cat /tmp/jarvis-server.pid)" 2>/dev/null || true
      rm -f /tmp/jarvis-server.pid
    fi
    fuser -k 8765/tcp 2>/dev/null || true
    sleep 1
    export JARVIS_NO_SANDBOX=1
    export JARVIS_OWNER=ulrich
    PYTHONUNBUFFERED=1 python3 -u -m src.server.web_server > /tmp/jarvis-clean.log 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > /tmp/jarvis-server.pid
    echo "Server restarted (PID $NEW_PID)"
    # Wait for it to come up
    for i in $(seq 1 20); do
      if curl -s http://localhost:8765/ > /dev/null 2>&1; then
        echo "Server is up"
        break
      fi
      sleep 1
    done
    ;;
  --extension)
    echo "Chrome extension updated — reload manually at chrome://extensions"
    ;;
  --all)
    bash "$0" --python
    bash "$0" --frontend
    ;;
  *)
    echo "Usage: self-deploy.sh [--python|--frontend|--server|--extension|--all]"
    echo "  --python     Signal server to reload Python modules (default, instant)"
    echo "  --frontend   Rebuild React frontend"
    echo "  --server     Full server restart (slower, needed for route changes)"
    echo "  --extension  Reminder to reload Chrome extension"
    echo "  --all        Python + frontend"
    exit 1
    ;;
esac

echo "Self-deploy complete: $MODE"
