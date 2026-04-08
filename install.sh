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
JARVIS_DIR="$HOME/.local/share/jarvis"
JARVIS_HOME="$HOME/.jarvis"
EXTENSION_DST="$JARVIS_HOME/extension"
EXTENSION_SRC="extensions/jarvis-screen"

echo ""
echo -e "  ${CYAN}╦╔═╗╦═╗╦  ╦╦╔═╗${RESET}"
echo -e "  ${CYAN}║╠═╣╠╦╝╚╗╔╝║╚═╗${RESET}"
echo -e "  ${CYAN}╩ ╩╚═╝ ╚╝ ╩╚═╝${RESET}  ${DIM}Installer${RESET}"
echo ""
echo -e "  ${DIM}Autonomous AI agent — CLI · Desktop · Browser${RESET}"
echo ""

# ── Check dependencies ──────────────────────────────────────────────────────
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

# Check Python version
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "  ${RED}Python 3.10+ required. Found: $(python3 --version)${RESET}"
    exit 1
fi

# ── System packages ─────────────────────────────────────────────────────────
echo -e "  ${DIM}Installing system packages (desktop, audio, vision)...${RESET}"
if command -v apt &>/dev/null; then
    sudo apt install -y -qq \
        python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
        gir1.2-ayatanaappindicator3-0.1 \
        libcairo2-dev libgirepository1.0-dev \
        portaudio19-dev tesseract-ocr \
        ffmpeg 2>/dev/null || true
elif command -v dnf &>/dev/null; then
    sudo dnf install -y \
        python3-gobject gtk3 webkit2gtk4.1 \
        libayatana-appindicator-gtk3 \
        portaudio-devel tesseract ffmpeg 2>/dev/null || true
elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm \
        python-gobject gtk3 webkit2gtk-4.1 \
        portaudio tesseract ffmpeg 2>/dev/null || true
fi
echo ""

# ── Brain URL (Proxmox / remote server) ────────────────────────────────────
echo -e "  ${BOLD}Brain server${RESET}"
echo ""
echo -e "  ${DIM}JARVIS runs its brain on a server (Proxmox, VPS, or localhost).${RESET}"
echo -e "  ${DIM}All clients (CLI, Desktop, Browser) connect to this URL.${RESET}"
echo ""
read -p "  Brain URL [http://jarvis.local:8765]: " BRAIN_INPUT
BRAIN_URL="${BRAIN_INPUT:-http://jarvis.local:8765}"
BRAIN_URL="${BRAIN_URL%/}"
echo ""

# ── Clone or update ─────────────────────────────────────────────────────────
mkdir -p "$JARVIS_HOME/data" "$JARVIS_HOME/logs" "$HOME/.local/bin"

if [ -d "$JARVIS_DIR/.git" ]; then
    echo -e "  ${DIM}Updating JARVIS...${RESET}"
    git -C "$JARVIS_DIR" pull --quiet 2>/dev/null || true
else
    echo -e "  ${DIM}Downloading JARVIS...${RESET}"
    git clone --quiet --depth 1 "$JARVIS_REPO" "$JARVIS_DIR" 2>/dev/null || {
        # Running from local source
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
        if [ -d "$SCRIPT_DIR/src" ]; then
            echo -e "  ${DIM}Using local source...${RESET}"
            JARVIS_DIR="$SCRIPT_DIR"
        else
            echo -e "  ${RED}Failed to clone. Run from the JARVIS source directory.${RESET}"
            exit 1
        fi
    }
fi

# ── Python package ──────────────────────────────────────────────────────────
echo -e "  ${DIM}Installing Python package...${RESET}"
cd "$JARVIS_DIR"
pip3 install --quiet --break-system-packages -e ".[all]" 2>/dev/null || \
pip3 install --quiet -e ".[all]" 2>/dev/null || \
pip3 install --quiet --break-system-packages -e . 2>/dev/null || \
pip3 install --quiet -e . 2>/dev/null || true
echo -e "  ${GREEN}✓${RESET} Python package installed"

# ── Build frontend ──────────────────────────────────────────────────────────
if command -v npm &>/dev/null && [ -d "$JARVIS_DIR/src/server/frontend" ]; then
    echo -e "  ${DIM}Building frontend...${RESET}"
    cd "$JARVIS_DIR/src/server/frontend"
    npm install --quiet 2>/dev/null || true
    npm run build 2>/dev/null || true
    cd "$JARVIS_DIR"
    echo -e "  ${GREEN}✓${RESET} Frontend built"
fi

# ── Remote config ────────────────────────────────────────────────────────────
cat > "$JARVIS_HOME/remote.json" <<EOF
{
  "brain_url": "$BRAIN_URL"
}
EOF
echo -e "  ${GREEN}✓${RESET} Brain URL saved → $BRAIN_URL"

# ── AI Provider Setup ────────────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}AI providers ${DIM}(select multiple with commas: 1,3)${RESET}"
echo ""
echo -e "  ${CYAN}1${RESET}  Anthropic  ${DIM}(Claude — best quality)${RESET}"
echo -e "  ${CYAN}2${RESET}  OpenAI     ${DIM}(GPT-4o)${RESET}"
echo -e "  ${CYAN}3${RESET}  Groq       ${DIM}(Llama 3 — free tier, fast)${RESET}"
echo -e "  ${CYAN}4${RESET}  xAI        ${DIM}(Grok-3)${RESET}"
echo -e "  ${CYAN}5${RESET}  OpenRouter  ${DIM}(access all models)${RESET}"
echo -e "  ${CYAN}6${RESET}  Ollama     ${DIM}(local — free, private)${RESET}"
echo -e "  ${CYAN}7${RESET}  Skip       ${DIM}(configure later via UI)${RESET}"
echo ""
read -p "  Choice [default 6]: " AI_CHOICES
AI_CHOICES="${AI_CHOICES:-6}"

PROVIDERS="{"
PRIORITY=0
INSTALL_OLLAMA=false
OLLAMA_MODEL="qwen2.5:7b"

add_provider() {
    local name="$1" type="$2" key="$3" url="$4" model="$5" models="$6"
    if [ -n "$key" ]; then
        [ -n "$(echo "$PROVIDERS" | grep '"name"')" ] && PROVIDERS="$PROVIDERS,"
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
  }"
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
                "claude-haiku-4-5-20251001" '"claude-haiku-4-5-20251001","claude-sonnet-4-6","claude-opus-4-6"'
            ;;
        2)
            echo ""
            read -p "  OpenAI API key (sk-...): " KEY
            add_provider "openai" "openai" "$KEY" "https://api.openai.com/v1" \
                "gpt-4o-mini" '"gpt-4o","gpt-4o-mini"'
            ;;
        3)
            echo ""
            read -p "  Groq API key (gsk-...): " KEY
            add_provider "groq" "openai" "$KEY" "https://api.groq.com/openai/v1" \
                "llama-3.3-70b-versatile" '"llama-3.3-70b-versatile","llama-3.1-8b-instant"'
            ;;
        4)
            echo ""
            read -p "  xAI API key: " KEY
            add_provider "xai" "openai" "$KEY" "https://api.x.ai/v1" \
                "grok-3-mini" '"grok-3","grok-3-mini"'
            ;;
        5)
            echo ""
            read -p "  OpenRouter API key: " KEY
            add_provider "openrouter" "openai" "$KEY" "https://openrouter.ai/api/v1" \
                "anthropic/claude-sonnet-4" '"anthropic/claude-sonnet-4","openai/gpt-4o"'
            ;;
        6)
            INSTALL_OLLAMA=true
            echo ""
            echo -e "  ${DIM}Local model:${RESET}"
            echo -e "    ${CYAN}a${RESET}  qwen2.5:7b     ${DIM}(4.7GB — recommended)${RESET}"
            echo -e "    ${CYAN}b${RESET}  llama3.2:3b    ${DIM}(2.0GB — fastest)${RESET}"
            echo -e "    ${CYAN}c${RESET}  qwen2.5:72b    ${DIM}(47GB — best, needs 64GB RAM)${RESET}"
            read -p "  Model [a]: " MC
            case "${MC:-a}" in
                b) OLLAMA_MODEL="llama3.2:3b" ;;
                c) OLLAMA_MODEL="qwen2.5:72b" ;;
                *) OLLAMA_MODEL="qwen2.5:7b" ;;
            esac
            [ -n "$(echo "$PROVIDERS" | grep '"name"')" ] && PROVIDERS="$PROVIDERS,"
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
  }"
            PRIORITY=$((PRIORITY + 1))
            echo -e "  ${GREEN}✓${RESET} Ollama ($OLLAMA_MODEL) configured"
            ;;
        7)
            echo -e "  ${DIM}Skipped — say 'setup providers' to JARVIS to configure later.${RESET}"
            ;;
    esac
done

PROVIDERS="$PROVIDERS
}"

if [ ! -f "$JARVIS_HOME/providers.json" ]; then
    printf '%s\n' "$PROVIDERS" > "$JARVIS_HOME/providers.json"
    echo -e "  ${GREEN}✓${RESET} Provider config saved"
fi

# ── Ollama install ───────────────────────────────────────────────────────────
if [ "$INSTALL_OLLAMA" = true ]; then
    if ! command -v ollama &>/dev/null; then
        echo ""
        echo -e "  ${DIM}Installing Ollama...${RESET}"
        curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null || \
            echo -e "  ${YELLOW}Ollama install failed — install manually: https://ollama.ai${RESET}"
    fi
    if command -v ollama &>/dev/null; then
        echo -e "  ${DIM}Pulling $OLLAMA_MODEL (may take a while)...${RESET}"
        ollama pull "$OLLAMA_MODEL" 2>/dev/null || true
        echo -e "  ${GREEN}✓${RESET} Local model ready"
    fi
fi

# ── PipeWire echo cancellation ───────────────────────────────────────────────
mkdir -p "$HOME/.config/pipewire/pipewire.conf.d"
if [ ! -f "$HOME/.config/pipewire/pipewire.conf.d/jarvis-echo-cancel.conf" ]; then
    cat > "$HOME/.config/pipewire/pipewire.conf.d/jarvis-echo-cancel.conf" <<'AECCONF'
context.modules = [
    {
        name = libpipewire-module-echo-cancel
        args = {
            library.name = aec/libspa-aec-webrtc
            node.latency = 1024/48000
            monitor.mode = true
            source.props = { node.name = "Echo Cancellation Source" }
            sink.props   = { node.name = "Echo Cancellation Sink" }
        }
    }
]
AECCONF
    systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || true
    echo -e "  ${GREEN}✓${RESET} PipeWire echo cancellation configured"
fi

# ── PATH ────────────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    SHELL_RC=""
    [ -f "$HOME/.zshrc" ]  && SHELL_RC="$HOME/.zshrc"
    [ -f "$HOME/.bashrc" ] && SHELL_RC="$HOME/.bashrc"
    if [ -n "$SHELL_RC" ] && ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    fi
fi

# ── Desktop autostart (single, clean entry) ─────────────────────────────────
mkdir -p "$HOME/.config/autostart"

# Remove stale duplicate if it exists
rm -f "$HOME/.config/autostart/jarvis-desktop.desktop"

cat > "$HOME/.config/autostart/jarvis.desktop" <<AUTOSTART
[Desktop Entry]
Type=Application
Name=JARVIS
Comment=J.A.R.V.I.S. AI Assistant
Exec=$JARVIS_DIR/scripts/start-jarvis.sh
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-XFCE-Autostart-Override=true
AUTOSTART
echo -e "  ${GREEN}✓${RESET} Desktop autostart configured (single entry)"

# ── Browser extension ────────────────────────────────────────────────────────
if [ -d "$JARVIS_DIR/$EXTENSION_SRC" ]; then
    mkdir -p "$EXTENSION_DST"
    cp -r "$JARVIS_DIR/$EXTENSION_SRC/." "$EXTENSION_DST/"
    # Bake brain URL into a config file the extension reads on first load
    cat > "$EXTENSION_DST/config.json" <<EOF
{"brain_url": "$BRAIN_URL"}
EOF
    echo -e "  ${GREEN}✓${RESET} Browser extension → $EXTENSION_DST"
fi

# ── Done ────────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}${BOLD}✔ JARVIS installed!${RESET}"
echo ""
echo -e "  ${BOLD}CLI${RESET}      run ${CYAN}jarvis${RESET}"
echo -e "  ${BOLD}Web UI${RESET}   ${CYAN}$BRAIN_URL${RESET}"
echo -e "  ${BOLD}Desktop${RESET}  auto-starts on login  (run ${CYAN}$JARVIS_DIR/scripts/start-jarvis.sh${RESET} now)"
echo ""
echo -e "  ${BOLD}Browser extension${RESET} — load from: ${CYAN}$EXTENSION_DST${RESET}"
echo ""
echo -e "  ${DIM}Chrome:  chrome://extensions  → Developer mode → Load unpacked → select above folder${RESET}"
echo -e "  ${DIM}Firefox: about:debugging       → This Firefox → Load Temporary Add-on → manifest.json${RESET}"
echo ""
echo -e "  ${DIM}Restart your shell or run:${RESET}  ${CYAN}source ~/.zshrc${RESET}"
echo ""
