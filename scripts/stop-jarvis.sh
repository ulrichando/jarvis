#!/bin/bash
# Stop all JARVIS processes reliably
echo "Stopping JARVIS..."

# Kill by PID file
if [ -f /tmp/jarvis-server.pid ]; then
    PID=$(cat /tmp/jarvis-server.pid)
    kill -9 "$PID" 2>/dev/null && echo "  Server (PID $PID) killed"
    rm -f /tmp/jarvis-server.pid
fi

# Kill by port
fuser -k -9 8765/tcp 2>/dev/null && echo "  Port 8765 freed"

# Kill by process name
pkill -9 -f "src.server.web_server" 2>/dev/null
pkill -9 -f "src.desktop.app" 2>/dev/null
pkill -9 -f "desktop.app.*main" 2>/dev/null

echo "JARVIS stopped."
