#!/usr/bin/env bash
# ╦╔═╗╦═╗╦  ╦╦╔═╗  BOOTSTRAP
# ║╠═╣╠╦╝╚╗╔╝║╚═╗  Self-reconstruction script
# ╚╝╩ ╩╩╚═ ╚╝ ╩╚═╝  Run this on ANY machine to install JARVIS
#
# Usage: curl -sL YOUR_URL/bootstrap.sh | bash
# Or:    ./bootstrap.sh

set -e

JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
JARVIS_REPO="$JARVIS_HOME/jarvis"
CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

say() { echo -e "${CYAN}[JARVIS]${NC} $1"; }
ok()  { echo -e "${GREEN}[OK]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Detect OS ──────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)   OS="linux";;
        Darwin*)  OS="macos";;
        MINGW*|MSYS*|CYGWIN*) OS="windows";;
        *)        OS="unknown";;
    esac

    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO="$ID"
    elif [ "$OS" = "macos" ]; then
        DISTRO="macos"
    else
        DISTRO="unknown"
    fi

    say "Detected: $OS ($DISTRO)"
}

# ── Install system dependencies ────────────────────────────────
install_deps() {
    say "Installing system dependencies..."

    case "$DISTRO" in
        ubuntu|debian|kali|pop|mint|elementary)
            sudo apt-get update -qq
            sudo apt-get install -y -qq python3 python3-venv python3-pip \
                portaudio19-dev espeak-ng curl git
            ;;
        fedora|rhel|centos)
            sudo dnf install -y python3 python3-pip \
                portaudio-devel espeak-ng curl git
            ;;
        arch|manjaro)
            sudo pacman -S --noconfirm python python-pip \
                portaudio espeak-ng curl git
            ;;
        macos)
            if ! command -v brew &>/dev/null; then
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            fi
            brew install python portaudio espeak git
            ;;
        *)
            err "Unsupported distro: $DISTRO. Install Python 3.10+ manually."
            ;;
    esac

    ok "System dependencies installed"
}

# ── Install Rust (if not present) ──────────────────────────────
install_rust() {
    if command -v cargo &>/dev/null; then
        ok "Rust already installed"
        return
    fi
    say "Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
    ok "Rust installed"
}

# ── Setup JARVIS ───────────────────────────────────────────────
setup_jarvis() {
    say "Setting up JARVIS at $JARVIS_REPO..."
    mkdir -p "$JARVIS_HOME"

    if [ -d "$JARVIS_REPO" ]; then
        say "JARVIS directory exists, updating..."
        cd "$JARVIS_REPO"
    else
        say "JARVIS not found. Creating from embedded source..."
        mkdir -p "$JARVIS_REPO"
        cd "$JARVIS_REPO"
        # If this script is run standalone, the user needs to copy the source
        # or we clone from a git repo
    fi

    # Create Python venv
    say "Creating Python environment..."
    python3 -m venv .venv
    source .venv/bin/activate

    # Install Python deps
    pip install --quiet groq aiohttp rich requests beautifulsoup4 duckduckgo-search

    ok "JARVIS environment ready"
}

# ── Configure ──────────────────────────────────────────────────
configure() {
    ENV_FILE="$JARVIS_REPO/.env"
    if [ ! -f "$ENV_FILE" ]; then
        say "First time setup — need your Groq API key."
        echo -n "Groq API key (from console.groq.com): "
        read -r API_KEY
        echo "GROQ_API_KEY=$API_KEY" > "$ENV_FILE"
        ok "API key saved to .env"
    else
        ok "Config exists"
    fi
}

# ── Create launcher ────────────────────────────────────────────
create_launcher() {
    LAUNCHER="$HOME/.local/bin/jarvis"
    mkdir -p "$HOME/.local/bin"

    cat > "$LAUNCHER" << 'SCRIPT'
#!/usr/bin/env bash
JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
cd "$JARVIS_HOME/jarvis"
source .venv/bin/activate

case "${1:-web}" in
    web)    python -m shells.web.server ;;
    cli)    python -m shells.terminal.cli ;;
    evolve) python -c "
import asyncio, sys; sys.path.insert(0,'.')
from brain.main import Brain
async def run():
    b = Brain(); await b.start()
    r = await b.evolve()
    print(r)
asyncio.run(run())
" ;;
    *)      echo "Usage: jarvis [web|cli|evolve]" ;;
esac
SCRIPT

    chmod +x "$LAUNCHER"
    ok "Launcher created: $LAUNCHER"
    say "Run 'jarvis web' or 'jarvis cli' to start"
}

# ── Systemd service (Linux) ────────────────────────────────────
create_service() {
    if [ "$OS" != "linux" ]; then return; fi

    SERVICE_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SERVICE_DIR"

    cat > "$SERVICE_DIR/jarvis.service" << EOF
[Unit]
Description=JARVIS AI Brain
After=network.target

[Service]
Type=simple
WorkingDirectory=$JARVIS_REPO
ExecStart=$JARVIS_REPO/.venv/bin/python -m shells.web.server
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable jarvis.service
    ok "Systemd service created. Start with: systemctl --user start jarvis"
}

# ── Main ───────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${CYAN}  ╦╔═╗╦═╗╦  ╦╦╔═╗${NC}"
    echo -e "${CYAN}  ║╠═╣╠╦╝╚╗╔╝║╚═╗${NC}"
    echo -e "${CYAN} ╚╝╩ ╩╩╚═ ╚╝ ╩╚═╝${NC}"
    echo -e "  Self-Reconstruction"
    echo ""

    detect_os
    install_deps
    install_rust
    setup_jarvis
    configure
    create_launcher
    create_service

    echo ""
    ok "JARVIS is ready."
    say "Start with: jarvis web"
    say "Or:         jarvis cli"
    echo ""
}

main "$@"
