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

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # Already up to date
fi

echo "$LOG_PREFIX New version detected: $LOCAL → $REMOTE"

# Pull latest
git pull origin master --quiet

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

# Restart desktop overlay if running
if pgrep -f "jarvis.*desktop\|desktop.*jarvis\|app.py" &>/dev/null; then
  echo "$LOG_PREFIX Restarting desktop overlay..."
  pkill -f "jarvis.*desktop\|desktop.*jarvis\|src.desktop.app" 2>/dev/null || true
  sleep 1
  nohup python -c "from src.desktop.app import main; main()" \
    > /tmp/jarvis-desktop.log 2>&1 &
  echo "$LOG_PREFIX Desktop restarted"
fi

echo "$LOG_PREFIX Update complete → $(git rev-parse --short HEAD)"
