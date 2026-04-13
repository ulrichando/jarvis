#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CLI_DIR="${PROJECT_ROOT}/src/cli-ts"

# Check if backend is up; start it if not
if ! curl -sf http://localhost:8765/api/ready > /dev/null 2>&1; then
    echo "[JARVIS] Backend not detected — starting it..."
    "${SCRIPT_DIR}/start-jarvis.sh" &
    echo "[JARVIS] Waiting for backend..."
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8765/api/ready > /dev/null 2>&1; then
            echo "[JARVIS] Backend is ready."
            break
        fi
        sleep 1
    done
fi

cd "${CLI_DIR}"

if [ ! -d "node_modules" ]; then
    echo "[JARVIS] Installing Node dependencies..."
    npm install
fi

MODE="${1:-dev}"

if [ "${MODE}" = "prod" ]; then
    echo "[JARVIS] Starting CLI (production)..."
    npm start
else
    echo "[JARVIS] Starting CLI (dev/hot-reload)..."
    npm run dev
fi
