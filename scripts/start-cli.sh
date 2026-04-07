#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CLI_DIR="${PROJECT_ROOT}/src/cli-ts"

# Check if backend is up; start it if not
if ! curl -sf http://localhost:8765/api/providers > /dev/null 2>&1; then
    echo "[JARVIS] Backend not detected — starting it first..."
    "${SCRIPT_DIR}/start-backend.sh"
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
