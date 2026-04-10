#!/usr/bin/env bash
# JARVIS Auto-Updater — checks for new commits, pulls and restarts if needed
# Run via systemd timer every 5 minutes

set -euo pipefail

JARVIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_PREFIX="[JARVIS UPDATE]"

cd "$JARVIS_DIR"

# Fetch remote silently
git fetch origin master --quiet 2>/dev/null || {
  echo "$LOG_PREFIX fetch failed (offline?)"
  exit 0
}

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

# Also check if the server has a newer version deployed (even if local is current)
SERVER_COMMIT=$(curl -sf --max-time 5 "https://jarvis.0wlan.com/api/version" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('commit',''))" 2>/dev/null || echo "")
KNOWN_SERVER=$(cat /tmp/.jarvis_server_commit 2>/dev/null || echo "")

SERVER_UPDATED=false
if [ -n "$SERVER_COMMIT" ] && [ "$SERVER_COMMIT" != "$KNOWN_SERVER" ] && [ "$SERVER_COMMIT" != "unknown" ]; then
  echo "$LOG_PREFIX Server updated: $KNOWN_SERVER → $SERVER_COMMIT"
  echo "$SERVER_COMMIT" > /tmp/.jarvis_server_commit
  SERVER_UPDATED=true
fi

if [ "$LOCAL" = "$REMOTE" ] && [ "$SERVER_UPDATED" = "false" ]; then
  exit 0  # Nothing changed
fi

if [ "$LOCAL" != "$REMOTE" ]; then
  echo "$LOG_PREFIX New version detected: $LOCAL → $REMOTE"
  # Pull latest
  git pull origin master --quiet
fi

# Reinstall if dependencies changed (pyproject.toml or requirements)
if git diff "$LOCAL" HEAD -- pyproject.toml setup.py requirements*.txt &>/dev/null | grep -q .; then
  echo "$LOG_PREFIX Dependencies changed — reinstalling..."
  pip install -e . --quiet
fi

# Rebuild frontend if source changed
if git diff "$LOCAL" HEAD -- src/server/frontend/src/ &>/dev/null | grep -q .; then
  echo "$LOG_PREFIX Frontend changed — rebuilding..."
  cd "$JARVIS_DIR/src/server/frontend"
  npm ci --prefer-offline --quiet
  npm run build --quiet
  cd "$JARVIS_DIR"
fi

# Notify desktop (send a desktop notification)
notify-send "JARVIS Updated" "New version pulled: $(git rev-parse --short HEAD)" \
  --icon=dialog-information --expire-time=5000 2>/dev/null || true

# Restart desktop overlay if running (on any update — local or server)
if pgrep -f "jarvis.*desktop\|desktop.*jarvis\|src.desktop.app" &>/dev/null; then
  echo "$LOG_PREFIX Restarting desktop overlay..."
  pkill -f "jarvis.*desktop\|desktop.*jarvis\|src.desktop.app" 2>/dev/null || true
  sleep 1
  nohup python -m src.desktop.app \
    > /tmp/jarvis-desktop.log 2>&1 &
  echo "$LOG_PREFIX Desktop restarted"
elif [ "$SERVER_UPDATED" = "true" ]; then
  echo "$LOG_PREFIX Server updated but desktop not running — skipping restart"
fi

echo "$LOG_PREFIX Update complete → $(git rev-parse --short HEAD)"
