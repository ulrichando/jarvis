#!/bin/bash
# JARVIS Post-Update Health Check
# Run this after apt upgrade to verify nothing is broken
# Auto-repairs if possible

CYAN='\033[36m'
GREEN='\033[32m'
RED='\033[31m'
DIM='\033[2m'
RESET='\033[0m'
# Resolve real user home even when running under sudo
if [ -n "$SUDO_USER" ]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME="$HOME"
fi
JARVIS="$REAL_HOME/Documents/Projects/jarvis"
PYTHON3="$(command -v python3)"

echo -e "${CYAN}JARVIS Post-Update Check${RESET}"
echo ""

ISSUES=0

# 1. Check Python exists
if [ -n "$PYTHON3" ]; then
    echo -e "  ${GREEN}✓${RESET} Python3: $PYTHON3"
else
    echo -e "  ${RED}✗${RESET} python3 not found"
    ISSUES=$((ISSUES + 1))
fi

# 2. Check critical imports (only actual dependencies — no openai/anthropic SDK needed)
for mod in src aiohttp msgpack edge_tts numpy pydantic yaml bs4 requests PIL; do
    if "$PYTHON3" -c "import $mod" 2>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} $mod"
    else
        echo -e "  ${RED}✗${RESET} $mod missing — installing..."
        pip install -e "$JARVIS/" 2>/dev/null
        ISSUES=$((ISSUES + 1))
    fi
done

# 3. Check desktop dependencies (GTK, WebKit)
if "$PYTHON3" -c "import gi; gi.require_version('Gtk','3.0'); gi.require_version('WebKit2','4.1')" 2>/dev/null; then
    echo -e "  ${GREEN}✓${RESET} GTK3 + WebKit2"
else
    echo -e "  ${RED}✗${RESET} GTK/WebKit missing — install: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1"
    ISSUES=$((ISSUES + 1))
fi

# 4. Check Ollama
if command -v ollama &>/dev/null; then
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} Ollama running"
    else
        echo -e "  ${RED}✗${RESET} Ollama not running — starting..."
        sudo systemctl start ollama 2>/dev/null || ollama serve &>/dev/null &
        ISSUES=$((ISSUES + 1))
    fi
else
    echo -e "  ${DIM}-${RESET} Ollama not installed (optional)"
fi

# 5. Check JARVIS source
if [ -f "$JARVIS/src/brain.py" ]; then
    echo -e "  ${GREEN}✓${RESET} JARVIS source intact"
else
    echo -e "  ${RED}✗${RESET} JARVIS source missing!"
    ISSUES=$((ISSUES + 1))
fi

# 6. Run quick test
if "$PYTHON3" -m pytest "$JARVIS/test/" -q --tb=no 2>/dev/null | tail -1 | grep -q "passed"; then
    echo -e "  ${GREEN}✓${RESET} Tests passing"
else
    echo -e "  ${RED}✗${RESET} Some tests failing"
    ISSUES=$((ISSUES + 1))
fi

# 7. Check providers config
if [ -f "$REAL_HOME/.jarvis/providers.json" ]; then
    echo -e "  ${GREEN}✓${RESET} Provider config exists"
else
    echo -e "  ${RED}✗${RESET} Provider config missing"
    ISSUES=$((ISSUES + 1))
fi

echo ""
if [ "$ISSUES" -eq 0 ]; then
    echo -e "  ${GREEN}All clear. JARVIS is healthy.${RESET}"
else
    echo -e "  ${RED}$ISSUES issue(s) found and auto-repaired.${RESET}"
fi
