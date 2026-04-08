#!/usr/bin/env bash
#
# JARVIS Installer — one command to install JARVIS on any Linux system
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | bash
#
# Or locally:
#   bash install.sh
#

set -e

CYAN='\033[36m'
GREEN='\033[32m'
RED='\033[31m'
YELLOW='\033[33m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

JARVIS_REPO="https://github.com/ulrichando/jarvis.git"
JARVIS_DIR="$HOME/.jarvis-src"
JARVIS_BIN="$HOME/.local/bin/jarvis"

echo ""
echo -e "  ${CYAN}╔═▓▓▓▓═╗${RESET}"
echo -e "  ${CYAN}║ ${BOLD}J.A.R.V.I.S${RESET}${CYAN} ║${RESET}"
echo -e "  ${CYAN}╚═▓▓▓▓═╝${RESET}"
echo ""
echo -e "  ${DIM}Installing JARVIS — your personal AI agent${RESET}"
echo ""

# ── Check dependencies ──
check_cmd() {
    if command -v "$1" &>/dev/null; then
        echo -e "  ${GREEN}✓${RESET} $1"
        return 0
    else
        echo -e "  ${RED}✗${RESET} $1 ${DIM}(missing)${RESET}"
        return 1
    fi
}

echo -e "  ${DIM}Checking dependencies...${RESET}"
MISSING=0
check_cmd python3 || MISSING=1
check_cmd pip3 || check_cmd pip || MISSING=1
check_cmd git || MISSING=1
echo ""

if [ "$MISSING" -eq 1 ]; then
    echo -e "  ${RED}Missing dependencies. Installing...${RESET}"
    if command -v apt &>/dev/null; then
        sudo apt update -qq && sudo apt install -y -qq python3 python3-pip git
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3 python3-pip git
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm python python-pip git
    elif command -v brew &>/dev/null; then
        brew install python git
    else
        echo -e "  ${RED}Cannot auto-install. Please install python3, pip, and git manually.${RESET}"
        exit 1
    fi
    echo ""
fi

# ── Install system dependencies for desktop overlay ──
echo -e "  ${DIM}Installing system packages (desktop, audio, vision)...${RESET}"
if command -v apt &>/dev/null; then
    sudo apt install -y -qq \
        python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
        gir1.2-ayatanaappindicator3-0.1 \
        libcairo2-dev libgirepository1.0-dev \
        portaudio19-dev tesseract-ocr \
        ffmpeg libspeexdsp-dev \
        pipewire-module-echo-cancel 2>/dev/null || true
elif command -v dnf &>/dev/null; then
    sudo dnf install -y \
        python3-gobject gtk3 webkit2gtk4.1 \
        libayatana-appindicator-gtk3 \
        cairo-devel gobject-introspection-devel \
        portaudio-devel tesseract \
        ffmpeg speexdsp-devel 2>/dev/null || true
elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm \
        python-gobject gtk3 webkit2gtk-4.1 \
        libappindicator-gtk3 \
        cairo gobject-introspection \
        portaudio tesseract \
        ffmpeg speexdsp 2>/dev/null || true
fi
echo ""

# ── Clone or update repo ──
if [ -d "$JARVIS_DIR" ]; then
    echo -e "  ${DIM}Updating JARVIS...${RESET}"
    cd "$JARVIS_DIR" && git pull --quiet 2>/dev/null || true
else
    echo -e "  ${DIM}Downloading JARVIS...${RESET}"
    git clone --quiet --depth 1 "$JARVIS_REPO" "$JARVIS_DIR" 2>/dev/null || {
        if [ -d "$(dirname "$0")/src" ]; then
            echo -e "  ${DIM}Using local source...${RESET}"
            JARVIS_DIR="$(cd "$(dirname "$0")" && pwd)"
        else
            echo -e "  ${RED}Failed to clone repo. Using local install.${RESET}"
            JARVIS_DIR="$(pwd)"
        fi
    }
fi

# ── Install Python dependencies ──
echo -e "  ${DIM}Installing Python packages...${RESET}"
cd "$JARVIS_DIR"
if [ -f "requirements.lock" ]; then
    pip3 install --quiet --break-system-packages -r requirements.lock 2>/dev/null || \
    pip3 install --quiet -r requirements.lock 2>/dev/null || \
    pip install --quiet --break-system-packages -r requirements.lock 2>/dev/null || true
fi

# Install JARVIS itself (hatchling build, no egg-info)
pip3 install --quiet --break-system-packages . 2>/dev/null || \
pip3 install --quiet . 2>/dev/null || \
pip install --quiet --break-system-packages . 2>/dev/null || true

# ── Build frontend if npm available ──
if command -v npm &>/dev/null && [ -d "$JARVIS_DIR/src/server/frontend" ]; then
    echo -e "  ${DIM}Building frontend...${RESET}"
    cd "$JARVIS_DIR/src/server/frontend"
    npm install --quiet 2>/dev/null || true
    npm run build 2>/dev/null || true
    cd "$JARVIS_DIR"
    echo -e "  ${GREEN}✓${RESET} Frontend built"
fi

# ── Setup PipeWire echo cancellation ──
echo -e "  ${DIM}Configuring echo cancellation...${RESET}"
mkdir -p "$HOME/.config/pipewire/pipewire.conf.d"
if [ ! -f "$HOME/.config/pipewire/pipewire.conf.d/jarvis-echo-cancel.conf" ]; then
    cat > "$HOME/.config/pipewire/pipewire.conf.d/jarvis-echo-cancel.conf" << 'AECCONF'
context.modules = [
    {
        name = libpipewire-module-echo-cancel
        args = {
            library.name = aec/libspa-aec-webrtc
            node.latency = 1024/48000
            monitor.mode = true
            capture.props = {
                node.name = "Echo Cancellation Capture"
            }
            source.props = {
                node.name = "Echo Cancellation Source"
                node.description = "JARVIS Mic (Echo Cancelled)"
            }
            sink.props = {
                node.name = "Echo Cancellation Sink"
            }
            playback.props = {
                node.name = "Echo Cancellation Playback"
            }
        }
    }
]
AECCONF
    echo -e "  ${GREEN}✓${RESET} PipeWire WebRTC echo cancellation configured"
    # Restart PipeWire to load the module
    systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || true
    sleep 2
    # Set echo-cancelled source as default
    pactl set-default-source "Echo Cancellation Source" 2>/dev/null || true
fi

# ── Ensure ~/.local/bin is in PATH ──
mkdir -p "$HOME/.local/bin"
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    SHELL_RC=""
    if [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        SHELL_RC="$HOME/.bashrc"
    fi
    if [ -n "$SHELL_RC" ]; then
        if ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            echo -e "  ${DIM}Added ~/.local/bin to PATH in $(basename "$SHELL_RC")${RESET}"
        fi
    fi
fi

# ── AI Provider Setup (interactive) ──
echo ""
echo -e "  ${BOLD}Choose your AI providers ${DIM}(select multiple with commas: 1,3,5)${RESET}"
echo ""
echo -e "  ${CYAN}1${RESET}  Anthropic  ${DIM}(Claude — best quality)${RESET}"
echo -e "  ${CYAN}2${RESET}  OpenAI     ${DIM}(GPT-4o, GPT-4o-mini)${RESET}"
echo -e "  ${CYAN}3${RESET}  Groq       ${DIM}(Llama 3.3 — free tier, fast)${RESET}"
echo -e "  ${CYAN}4${RESET}  xAI        ${DIM}(Grok-3)${RESET}"
echo -e "  ${CYAN}5${RESET}  OpenRouter  ${DIM}(access all models via one key)${RESET}"
echo -e "  ${CYAN}6${RESET}  Ollama     ${DIM}(local — free, private, no API key needed)${RESET}"
echo -e "  ${CYAN}7${RESET}  Custom     ${DIM}(any OpenAI-compatible server)${RESET}"
echo -e "  ${CYAN}8${RESET}  Skip       ${DIM}(configure later via the UI — say 'setup' to JARVIS)${RESET}"
echo ""
read -p "  Choice (e.g. 1,6 — default 6): " AI_CHOICES
AI_CHOICES="${AI_CHOICES:-6}"

mkdir -p "$HOME/.jarvis/data" "$HOME/.jarvis/logs"

# Parse choices
PROVIDERS="{"
PRIORITY=0
INSTALL_OLLAMA=false
OLLAMA_MODEL="qwen2.5:7b"

add_provider() {
    local name="$1" type="$2" key="$3" url="$4" model="$5" models="$6"
    if [ -n "$key" ]; then
        PROVIDERS="$PROVIDERS
  \"$name\": {
    \"name\": \"$name\",
    \"type\": \"$type\",
    \"api_key\": \"$key\",
    \"base_url\": \"$url\",
    \"model\": \"$model\",
    \"models\": [$models],
    \"priority\": $PRIORITY,
    \"enabled\": true
  },"
        PRIORITY=$((PRIORITY + 1))
        echo -e "  ${GREEN}✓${RESET} $name configured"
    fi
}

for choice in $(echo "$AI_CHOICES" | tr ',' ' '); do
    case "$choice" in
        1)
            echo ""
            read -p "  Anthropic API key (sk-ant-...): " KEY
            add_provider "claude" "anthropic" "$KEY" "https://api.anthropic.com" \
                "claude-haiku-4-5-20251001" '"claude-haiku-4-5-20251001", "claude-sonnet-4-20250514", "claude-opus-4-20250514"'
            ;;
        2)
            echo ""
            read -p "  OpenAI API key (sk-...): " KEY
            add_provider "openai" "openai" "$KEY" "https://api.openai.com/v1" \
                "gpt-4o-mini" '"gpt-4o", "gpt-4o-mini"'
            ;;
        3)
            echo ""
            read -p "  Groq API key (gsk-...): " KEY
            add_provider "groq" "openai" "$KEY" "https://api.groq.com/openai/v1" \
                "llama-3.1-8b-instant" '"llama-3.1-8b-instant"'
            ;;
        4)
            echo ""
            read -p "  xAI API key: " KEY
            add_provider "xai" "openai" "$KEY" "https://api.x.ai/v1" \
                "grok-3-mini" '"grok-3", "grok-3-mini"'
            ;;
        5)
            echo ""
            read -p "  OpenRouter API key: " KEY
            add_provider "openrouter" "openai" "$KEY" "https://openrouter.ai/api/v1" \
                "anthropic/claude-sonnet-4" '"anthropic/claude-sonnet-4", "openai/gpt-4o"'
            ;;
        6)
            INSTALL_OLLAMA=true
            echo ""
            echo -e "  ${DIM}Local models:${RESET}"
            echo -e "    ${CYAN}a${RESET}  qwen2.5:7b      ${DIM}(4.7GB — fast, good for chat + tools)${RESET}"
            echo -e "    ${CYAN}b${RESET}  llama3.2:3b     ${DIM}(2.0GB — tiny, very fast)${RESET}"
            echo -e "    ${CYAN}c${RESET}  deepseek-r1:14b  ${DIM}(8.9GB — reasoning)${RESET}"
            echo -e "    ${CYAN}d${RESET}  qwen2.5:72b     ${DIM}(47GB — best local, needs 64GB RAM)${RESET}"
            read -p "  Model (a-d, default a): " MC
            case "${MC:-a}" in
                b) OLLAMA_MODEL="llama3.2:3b" ;;
                c) OLLAMA_MODEL="deepseek-r1:14b" ;;
                d) OLLAMA_MODEL="qwen2.5:72b" ;;
                *) OLLAMA_MODEL="qwen2.5:7b" ;;
            esac
            PROVIDERS="$PROVIDERS
  \"ollama\": {
    \"name\": \"ollama\",
    \"type\": \"openai\",
    \"api_key\": \"ollama\",
    \"base_url\": \"http://localhost:11434/v1\",
    \"model\": \"$OLLAMA_MODEL\",
    \"models\": [\"$OLLAMA_MODEL\"],
    \"priority\": $PRIORITY,
    \"enabled\": true
  },"
            PRIORITY=$((PRIORITY + 1))
            echo -e "  ${GREEN}✓${RESET} Ollama ($OLLAMA_MODEL) configured"
            ;;
        7)
            echo ""
            read -p "  Provider name (e.g. lmstudio): " CUSTOM_NAME
            CUSTOM_NAME="${CUSTOM_NAME:-custom}"
            read -p "  Base URL (e.g. http://localhost:1234/v1): " CUSTOM_URL
            CUSTOM_URL="${CUSTOM_URL:-http://localhost:1234/v1}"
            read -p "  Model name: " CUSTOM_MODEL
            CUSTOM_MODEL="${CUSTOM_MODEL:-local-model}"
            read -p "  API key (Enter for none): " CUSTOM_KEY
            CUSTOM_KEY="${CUSTOM_KEY:-no-key}"
            add_provider "$CUSTOM_NAME" "openai" "$CUSTOM_KEY" "$CUSTOM_URL" \
                "$CUSTOM_MODEL" "\"$CUSTOM_MODEL\""
            ;;
        8)
            echo -e "  ${DIM}Skipped. Say 'setup' to JARVIS or press Ctrl+M to configure later.${RESET}"
            ;;
    esac
done

# Remove trailing comma and close JSON
PROVIDERS=$(echo "$PROVIDERS" | sed 's/,$//')
PROVIDERS="$PROVIDERS
}"

if [ ! -f "$HOME/.jarvis/providers.json" ]; then
    echo "$PROVIDERS" > "$HOME/.jarvis/providers.json"
    echo -e "  ${GREEN}✓${RESET} Provider config saved"
fi

# ── Install Ollama if selected ──
if [ "$INSTALL_OLLAMA" = true ]; then
    if ! command -v ollama &>/dev/null; then
        echo ""
        echo -e "  ${DIM}Installing Ollama (local AI runtime)...${RESET}"
        curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null || {
            echo -e "  ${YELLOW}Ollama install failed. Install manually: https://ollama.ai${RESET}"
        }
    fi
    if command -v ollama &>/dev/null; then
        echo -e "  ${DIM}Pulling $OLLAMA_MODEL (this may take a few minutes)...${RESET}"
        ollama pull "$OLLAMA_MODEL" 2>/dev/null || true
        echo -e "  ${GREEN}✓${RESET} Local model ready"
    fi
fi

# ── Create autostart entry ──
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/jarvis.desktop" << AUTOSTART
[Desktop Entry]
Type=Application
Name=JARVIS
Comment=JARVIS AI Assistant
Exec=$JARVIS_DIR/scripts/start-jarvis.sh
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
AUTOSTART
echo -e "  ${GREEN}✓${RESET} Autostart configured (JARVIS starts on login)"

# ── Done ──
echo ""
echo -e "  ${GREEN}${BOLD}JARVIS installed successfully!${RESET}"
echo ""
echo -e "  ${DIM}Commands:${RESET}"
echo -e "    ${CYAN}jarvis${RESET}                  Interactive CLI"
echo -e "    ${CYAN}jarvis-web${RESET}              Web server (http://localhost:8765)"
echo -e "    ${CYAN}jarvis-desktop${RESET}          Desktop overlay"
echo -e "    ${CYAN}bash scripts/start-jarvis.sh${RESET}  Full stack (audio + server + desktop)"
echo ""
echo -e "  ${DIM}Voice:${RESET}  Just speak — JARVIS listens and responds"
echo -e "  ${DIM}Setup:${RESET}  Say 'setup' or press Ctrl+M to change AI providers"
echo -e "  ${DIM}Vision:${RESET} Say 'turn on camera' to enable webcam"
echo ""
if [ "$AI_CHOICES" = "8" ]; then
    echo -e "  ${YELLOW}No AI provider configured yet.${RESET}"
    echo -e "  ${DIM}Say 'setup' to JARVIS or edit ~/.jarvis/providers.json${RESET}"
    echo ""
fi
echo -e "  ${DIM}Restart your shell or run:${RESET}  ${CYAN}source ~/.zshrc${RESET}"
echo ""
