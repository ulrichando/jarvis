#!/bin/bash
# JARVIS Full Startup — hardware-adaptive launch
# Auto-detects hardware and only enables available features.
# Manual: ./scripts/start-jarvis.sh

JARVIS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$JARVIS_ROOT"

echo "╦╔═╗╦═╗╦  ╦╦╔═╗"
echo "║╠═╣╠╦╝╚╗╔╝║╚═╗"
echo "╩ ╩╚═╝ ╚╝ ╩╚═╝"
echo ""

# ── Hardware detection ──
echo "[0/4] Detecting hardware..."
HW_JSON=$(python3 -c "
from src.hardware import detect_hardware
hw = detect_hardware()
import json
print(json.dumps({
    'mic': hw.has_microphone,
    'speakers': hw.has_speakers,
    'rgb_cam': hw.has_rgb_camera,
    'ir_cam': hw.has_ir_camera,
    'gpu': hw.has_nvidia,
    'display': bool(hw.display_server),
    'ram_gb': round(hw.total_ram_gb),
    'model_size': hw.recommended_model_size,
    'summary': hw.summary(),
}))
" 2>/dev/null)

if [ -z "$HW_JSON" ]; then
    echo "  Hardware detection failed — using defaults"
    HAS_MIC=true; HAS_DISPLAY=true; HAS_GPU=false
else
    echo "  $HW_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print('  ' + d['summary'])"
    HAS_MIC=$(echo "$HW_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['mic'])")
    HAS_DISPLAY=$(echo "$HW_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['display'])")
    HAS_GPU=$(echo "$HW_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['gpu'])")
fi
echo ""

# Wait for desktop to be ready (on boot, XFCE needs time)
sleep 2

# ── Step 1: Fix audio (only if mic/speakers detected) ──
if [ "$HAS_MIC" = "True" ]; then
    echo "[1/4] Fixing audio..."
    bash "$JARVIS_ROOT/scripts/fix-audio.sh" 2>/dev/null

    MIC_OK=$(python3 -c "
import sounddevice as sd, numpy as np
a = sd.rec(int(8000), samplerate=16000, channels=1, dtype='float32'); sd.wait()
print('OK' if np.max(np.abs(a)) > 0.001 else 'FAIL')
" 2>/dev/null)

    if [ "$MIC_OK" != "OK" ]; then
        echo "  Mic silent — retrying PipeWire..."
        systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null
        sleep 5
        bash "$JARVIS_ROOT/scripts/fix-audio.sh" 2>/dev/null
    fi
    echo "  Audio: OK"
else
    echo "[1/4] No microphone detected — skipping audio setup"
fi
echo ""

# ── Step 2: Check Ollama (local model fallback) ──
echo "[2/4] Checking Ollama..."
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  Ollama: running"
else
    echo "  Starting Ollama..."
    sudo systemctl start ollama 2>/dev/null || ollama serve &
    sleep 5
fi
echo ""

# ── Step 3: Start web server ──
echo "[3/4] Starting JARVIS server..."
# Kill ALL old instances reliably — by PID file, port, and process name
if [ -f /tmp/jarvis-server.pid ]; then
    kill -9 $(cat /tmp/jarvis-server.pid) 2>/dev/null
    rm -f /tmp/jarvis-server.pid
fi
fuser -k 8765/tcp 2>/dev/null
pkill -9 -f "src.server.web_server" 2>/dev/null
pkill -9 -f "python.*web_server" 2>/dev/null
sleep 2
# Verify port is free
if fuser 8765/tcp 2>/dev/null; then
    echo "  ERROR: Port 8765 still in use! Force killing..."
    fuser -k -9 8765/tcp 2>/dev/null
    sleep 2
fi
PYTHONUNBUFFERED=1 python3 -u -m src.server.web_server > /tmp/jarvis-clean.log 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > /tmp/jarvis-server.pid
echo "  Server PID: $SERVER_PID"

# Wait for HTTP
for i in $(seq 1 30); do
    if curl -s http://localhost:8765/ > /dev/null 2>&1; then
        echo "  HTTP: up"
        break
    fi
    sleep 1
done

# Wait for brain
echo "  Waiting for brain init..."
for i in $(seq 1 90); do
    READY=$(curl -s http://localhost:8765/api/ready 2>/dev/null | grep -o '"ready": true')
    if [ -n "$READY" ]; then
        echo "  Brain: ready"
        break
    fi
    sleep 1
done
echo ""

# ── Step 4: Launch desktop (only if display available) ──
if [ "$HAS_DISPLAY" = "True" ] && [ -n "$DISPLAY" ]; then
    echo "[4/4] Launching desktop..."
    pkill -f "src.desktop" 2>/dev/null
    sleep 1
    DISPLAY="${DISPLAY:-:0.0}" python3 -c "from src.desktop.app import main; main()" > /tmp/jarvis-desktop.log 2>&1 &
    DESKTOP_PID=$!
    echo "  Desktop PID: $DESKTOP_PID"
else
    echo "[4/4] No display — running headless (web-only)"
fi
echo ""

echo "JARVIS is online."
echo "  Web:      http://localhost:8765"
echo "  Server:   PID $SERVER_PID  →  tail -f /tmp/jarvis-clean.log"
[ -n "$DESKTOP_PID" ] && echo "  Desktop:  PID $DESKTOP_PID  →  tail -f /tmp/jarvis-desktop.log"
echo "  All logs: ./scripts/logs.sh [voice|llm|tools|errors]"
