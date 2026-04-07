#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

USE_DOCKER=false
if docker info > /dev/null 2>&1; then
    USE_DOCKER=true
fi

if [ "${USE_DOCKER}" = "true" ]; then
    echo "[JARVIS] Docker detected — starting backend via docker compose..."
    docker compose up -d

    echo "[JARVIS] Waiting for backend to be ready..."
    for i in $(seq 1 30); do
        if curl -s http://localhost:8765/api/health > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
else
    echo "[JARVIS] Docker not available — starting Python server directly..."
    python -m src.server.web_server &
    SERVER_PID=$!
    echo "[JARVIS] Python server started (PID: ${SERVER_PID})"

    echo "[JARVIS] Waiting for backend to be ready..."
    for i in $(seq 1 30); do
        if curl -s http://localhost:8765/api/health > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
fi

echo "[JARVIS] Backend ready at http://localhost:8765"
