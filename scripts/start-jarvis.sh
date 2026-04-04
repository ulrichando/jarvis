#!/bin/bash
# JARVIS Full Startup — fixes audio, starts server, launches desktop
# Auto-runs on boot via ~/.config/autostart/jarvis.desktop
# Manual: ./scripts/start-jarvis.sh

JARVIS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$JARVIS_ROOT"

echo "╦╔═╗╦═╗╦  ╦╦╔═╗"
echo "║╠═╣╠╦╝╚╗╔╝║╚═╗"
echo "╩ ╩╚═╝ ╚╝ ╩╚═╝"
echo ""

# Wait for desktop to be ready (on boot, XFCE needs time)
sleep 3

# Step 1: Fix audio FIRST — PipeWire loses devices on reboot
echo "[1/4] Fixing audio..."
bash "$JARVIS_ROOT/scripts/fix-audio.sh"

# Verify mic works before proceeding
MIC_OK=$(python3 -c "
import sounddevice as sd, numpy as np
a = sd.rec(int(8000), samplerate=16000, channels=1, dtype='float32'); sd.wait()
print('OK' if np.max(np.abs(a)) > 0.001 else 'FAIL')
" 2>/dev/null)

if [ "$MIC_OK" != "OK" ]; then
    echo "  Mic still silent — retrying PipeWire..."
    systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null
    sleep 5
    bash "$JARVIS_ROOT/scripts/fix-audio.sh"
fi
echo ""

# Step 2: Make sure Ollama is running
echo "[2/4] Checking Ollama..."
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  Ollama: running"
else
    echo "  Starting Ollama..."
    sudo systemctl start ollama 2>/dev/null || ollama serve &
    sleep 5
fi
echo ""

# Step 3: Kill any old instances, start web server
echo "[3/4] Starting JARVIS server..."
pkill -f "src.server.web_server" 2>/dev/null
sleep 1
PYTHONUNBUFFERED=1 python3 -m src.server.web_server > /tmp/jarvis-web.log 2>&1 &
SERVER_PID=$!
echo "  Server PID: $SERVER_PID"

# Wait for server
for i in $(seq 1 30); do
    if curl -s http://localhost:8765/api/mesh/ping > /dev/null 2>&1; then
        echo "  Server: online"
        break
    fi
    sleep 1
done
echo ""

# Step 4: Launch desktop app (single instance)
echo "[4/4] Launching desktop..."
pkill -f "src.desktop" 2>/dev/null
sleep 1
DISPLAY=:0.0 python3 -c "from src.desktop.app import main; main()" > /tmp/jarvis-desktop.log 2>&1 &
DESKTOP_PID=$!
echo "  Desktop PID: $DESKTOP_PID"
echo ""

echo "JARVIS is online."
echo "  Web:     http://localhost:8765"
echo "  Logs:    tail -f /tmp/jarvis-web.log"
