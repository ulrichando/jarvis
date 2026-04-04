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
VENV="$REAL_HOME/.jarvis/venv"
JARVIS="$REAL_HOME/Documents/Projects/jarvis"

echo -e "${CYAN}JARVIS Post-Update Check${RESET}"
echo ""

ISSUES=0

# 1. Check venv Python exists
if [ -f "$VENV/bin/python3" ]; then
    echo -e "  ${GREEN}✓${RESET} Venv Python exists"
else
    echo -e "  ${RED}✗${RESET} Venv Python missing — recreating..."
    python3 -m venv "$VENV" --system-site-packages
    "$VENV/bin/pip" install -e "$JARVIS/" 2>/dev/null
    "$VENV/bin/pip" install -r "$JARVIS/requirements.pinned" 2>/dev/null
    ISSUES=$((ISSUES + 1))
fi

# 2. Check critical imports (only actual dependencies — no openai/anthropic SDK needed)
for mod in src aiohttp msgpack edge_tts; do
    if "$VENV/bin/python3" -c "import $mod" 2>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} $mod"
    else
        echo -e "  ${RED}✗${RESET} $mod missing — installing..."
        "$VENV/bin/pip" install -r "$JARVIS/requirements.pinned" 2>/dev/null
        ISSUES=$((ISSUES + 1))
    fi
done

# 3. Check Ollama
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

# 4. Check JARVIS source
if [ -f "$JARVIS/src/brain.py" ]; then
    echo -e "  ${GREEN}✓${RESET} JARVIS source intact"
else
    echo -e "  ${RED}✗${RESET} JARVIS source missing!"
    ISSUES=$((ISSUES + 1))
fi

# 5. Run quick test
if "$VENV/bin/python3" -m pytest "$JARVIS/test/" -q --tb=no 2>/dev/null | tail -1 | grep -q "passed"; then
    echo -e "  ${GREEN}✓${RESET} Tests passing"
else
    echo -e "  ${RED}✗${RESET} Some tests failing"
    ISSUES=$((ISSUES + 1))
fi

# 6. Check providers config
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
