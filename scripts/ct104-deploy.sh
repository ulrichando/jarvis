#!/bin/bash
# JARVIS CT104 Deploy — triggered by Forgejo webhook or run manually
# Usage: bash scripts/ct104-deploy.sh
# On CT104: place this at /opt/jarvis/scripts/ct104-deploy.sh

set -euo pipefail

JARVIS_DIR="/opt/jarvis"
LOG="/tmp/jarvis-deploy.log"
COMPOSE="docker compose"

exec >> "$LOG" 2>&1
echo ""
echo "═══ JARVIS DEPLOY $(date) ═══"

cd "$JARVIS_DIR"

# Pull latest code
echo "[1/4] git pull..."
git pull origin master --quiet
echo "  → $(git rev-parse --short HEAD)"

# Rebuild image (skip cache on dependency changes)
echo "[2/4] Building image..."
$COMPOSE build --quiet jarvis

# Restart JARVIS only (leave Ollama running — it takes too long to restart)
echo "[3/4] Restarting JARVIS container..."
$COMPOSE up -d --no-deps jarvis

# Wait for health
echo "[4/4] Waiting for JARVIS to be ready..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8765/api/ready > /dev/null 2>&1; then
        echo "  ✔ JARVIS online (${i}s)"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  ✘ JARVIS did not come up in 60s — check logs:"
        echo "    docker compose logs --tail=50 jarvis"
        exit 1
    fi
    sleep 1
done

echo "Deploy complete → $(git rev-parse --short HEAD)"
