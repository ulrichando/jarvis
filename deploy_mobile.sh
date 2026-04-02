#!/usr/bin/env bash
# JARVIS Mobile Deployment
# Deploys JARVIS to a remote device (phone/tablet/other PC)
# via SSH or Termux (Android) or local network
#
# Usage:
#   ./deploy_mobile.sh android <IP>    — Deploy to Android via Termux
#   ./deploy_mobile.sh ssh <USER@IP>   — Deploy to any Linux/Mac via SSH
#   ./deploy_mobile.sh windows <IP>    — Deploy to Windows via SSH/WinRM
#   ./deploy_mobile.sh local           — Package for manual transfer

set -e

CYAN='\033[0;36m'
NC='\033[0m'
say() { echo -e "${CYAN}[JARVIS]${NC} $1"; }

JARVIS_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$1"
HOST="$2"

# Package JARVIS into a portable archive
package() {
    say "Packaging JARVIS..."
    ARCHIVE="/tmp/jarvis_deploy.tar.gz"
    tar -czf "$ARCHIVE" \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.git' \
        --exclude='data' \
        --exclude='target' \
        -C "$(dirname "$JARVIS_DIR")" "$(basename "$JARVIS_DIR")"
    echo "$ARCHIVE"
}

deploy_ssh() {
    local REMOTE="$1"
    say "Deploying JARVIS to $REMOTE via SSH..."

    ARCHIVE=$(package)

    # Copy
    scp "$ARCHIVE" "$REMOTE:/tmp/jarvis_deploy.tar.gz" || { err "Failed to copy archive"; exit 1; }

    # Install remotely
    ssh "$REMOTE" bash << 'REMOTE_SCRIPT'
set -e
echo "[JARVIS] Unpacking..."
mkdir -p ~/.jarvis
cd ~/.jarvis
tar -xzf /tmp/jarvis_deploy.tar.gz
cd jarvis

echo "[JARVIS] Setting up Python..."
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet groq aiohttp rich requests beautifulsoup4 duckduckgo-search edge-tts opencv-python-headless msgpack

echo "[JARVIS] Creating launcher..."
mkdir -p ~/.local/bin
cat > ~/.local/bin/jarvis << 'LAUNCHER'
#!/bin/bash
cd ~/.jarvis/jarvis
source .venv/bin/activate
case "${1:-web}" in
    web) python -m shells.web.server ;;
    cli) python -m shells.terminal.cli ;;
    *) echo "Usage: jarvis [web|cli]" ;;
esac
LAUNCHER
chmod +x ~/.local/bin/jarvis

echo "[JARVIS] Done! Run: jarvis web"
rm /tmp/jarvis_deploy.tar.gz
REMOTE_SCRIPT

    say "Deployed to $REMOTE. Run 'jarvis web' on that machine."
}

deploy_android() {
    local IP="$1"
    say "Deploying to Android (Termux) at $IP..."

    ARCHIVE=$(package)

    # Termux default SSH port is 8022
    scp -P 8022 "$ARCHIVE" "$IP:/data/data/com.termux/files/home/jarvis_deploy.tar.gz"

    ssh -p 8022 "$IP" bash << 'TERMUX_SCRIPT'
set -e
echo "[JARVIS] Installing on Termux..."
pkg install -y python python-pip

mkdir -p ~/jarvis
cd ~/
tar -xzf jarvis_deploy.tar.gz
cd jarvis

pip install groq aiohttp rich requests beautifulsoup4 duckduckgo-search edge-tts

echo "[JARVIS] Creating launcher..."
cat > ~/bin/jarvis << 'LAUNCHER'
#!/data/data/com.termux/files/usr/bin/bash
cd ~/jarvis
case "${1:-web}" in
    web) python -m shells.web.server ;;
    cli) python -m shells.terminal.cli ;;
esac
LAUNCHER
chmod +x ~/bin/jarvis

echo "[JARVIS] Done! Run: jarvis web"
rm ~/jarvis_deploy.tar.gz
TERMUX_SCRIPT

    say "Deployed to Android. Open Termux and run: jarvis web"
    say "Then open browser to http://$IP:8765"
}

deploy_local() {
    say "Packaging JARVIS for manual transfer..."
    ARCHIVE=$(package)
    # Also include the bootstrap script
    cp "$JARVIS_DIR/bootstrap.sh" /tmp/jarvis_bootstrap.sh

    say "Files ready:"
    say "  Archive: $ARCHIVE"
    say "  Bootstrap: /tmp/jarvis_bootstrap.sh"
    say ""
    say "Transfer both files to the target device, then run:"
    say "  tar -xzf jarvis_deploy.tar.gz && cd jarvis && ./bootstrap.sh"
}

case "$TARGET" in
    android)
        [ -z "$HOST" ] && { echo "Usage: $0 android <IP>"; exit 1; }
        deploy_android "$HOST"
        ;;
    ssh|linux|mac|macos)
        [ -z "$HOST" ] && { echo "Usage: $0 ssh <USER@IP>"; exit 1; }
        deploy_ssh "$HOST"
        ;;
    windows)
        [ -z "$HOST" ] && { echo "Usage: $0 windows <USER@IP>"; exit 1; }
        say "Windows deployment uses SSH. Make sure OpenSSH is enabled on Windows."
        deploy_ssh "$HOST"
        ;;
    local|package)
        deploy_local
        ;;
    *)
        echo "JARVIS Mobile Deployment"
        echo ""
        echo "Usage:"
        echo "  $0 android <IP>      Deploy to Android (Termux)"
        echo "  $0 ssh <USER@IP>     Deploy to Linux/Mac via SSH"
        echo "  $0 windows <USER@IP> Deploy to Windows via SSH"
        echo "  $0 local             Package for manual transfer"
        ;;
esac
