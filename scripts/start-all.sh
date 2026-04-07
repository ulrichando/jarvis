#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKEND_PID=""
USE_DOCKER=false

if docker info > /dev/null 2>&1; then
    USE_DOCKER=true
fi

cleanup() {
    echo ""
    echo "[JARVIS] Shutting down..."
    if [ "${USE_DOCKER}" = "true" ]; then
        cd "${PROJECT_ROOT}" && docker compose down
    elif [ -n "${BACKEND_PID}" ]; then
        kill "${BACKEND_PID}" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start backend in background
"${SCRIPT_DIR}/start-backend.sh" &
BACKEND_PID=$!

# Wait for backend to be ready (poll up to 30s)
echo "[JARVIS] Waiting for backend..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8765/api/providers > /dev/null 2>&1; then
        echo "[JARVIS] Backend is ready."
        break
    fi
    sleep 1
done

# Launch CLI (pass through any arguments)
"${SCRIPT_DIR}/start-cli.sh" "$@"
