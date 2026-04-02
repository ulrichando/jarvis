#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  JARVIS OS Setup Script                                     ║
# ║  Transforms a fresh Debian/Kali install into JARVIS OS      ║
# ║                                                              ║
# ║  Usage:                                                      ║
# ║    1. Install Debian 13 / Kali in VirtualBox (minimal)       ║
# ║    2. Copy this script + jarvis source into the VM           ║
# ║    3. Run: sudo bash setup-jarvis-os.sh                      ║
# ║    4. Reboot → JARVIS OS with arc reactor UI                 ║
# ╚══════════════════════════════════════════════════════════════╝
set -euo pipefail

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${CYAN}[JARVIS]${NC} $*"; }
ok()    { echo -e "${GREEN}[  OK  ]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL ]${NC} $*"; exit 1; }

# Must be root
[[ $EUID -eq 0 ]] || fail "Run as root: sudo bash $0"

# Detect source directory (script location or /opt/jarvis)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
JARVIS_SRC="${JARVIS_SRC:-$(dirname "$SCRIPT_DIR")}"
COGSCRIPT_SRC="${COGSCRIPT_SRC:-$(dirname "$JARVIS_SRC")/CogScript}"

# Target user
JARVIS_USER="${JARVIS_USER:-jarvis}"
if ! id "$JARVIS_USER" &>/dev/null; then
    info "Creating user '$JARVIS_USER'..."
    useradd -m -s /bin/bash -G sudo,audio,video,netdev "$JARVIS_USER"
    echo "$JARVIS_USER:jarvis" | chpasswd
    echo "$JARVIS_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/$JARVIS_USER
    ok "User '$JARVIS_USER' created"
fi

export DEBIAN_FRONTEND=noninteractive

echo
echo "╔══════════════════════════════════════════════╗"
echo "║  JARVIS OS — System Setup                    ║"
echo "╚══════════════════════════════════════════════╝"
echo

# ── Phase 1: Base packages ─────────────────────────────────────
info "Phase 1/7: Installing base packages..."
apt-get update -qq
apt-get install -y -qq \
    curl wget git build-essential pkg-config \
    sudo dbus dbus-user-session \
    ffmpeg espeak-ng \
    sqlite3 libsqlite3-dev \
    protobuf-compiler \
    unzip htop neofetch \
    libffi-dev libssl-dev \
    libasound2-dev portaudio19-dev \
    v4l-utils jq socat \
    python3 python3-venv python3-dev python3-pip \
    > /dev/null 2>&1
ok "Base packages installed"

# ── Phase 2: Desktop environment ───────────────────────────────
info "Phase 2/7: Installing desktop (Sway + Chromium)..."
apt-get install -y -qq \
    sway swaybg swaylock swayidle waybar foot \
    chromium xwayland wl-clipboard \
    fonts-noto fonts-noto-color-emoji fonts-firacode \
    grim slurp mako-notifier libnotify-bin \
    > /dev/null 2>&1
ok "Desktop environment installed"

# ── Phase 3: Audio (PipeWire) ──────────────────────────────────
info "Phase 3/7: Installing audio (PipeWire)..."
apt-get install -y -qq \
    pipewire pipewire-audio pipewire-pulse wireplumber \
    pavucontrol \
    > /dev/null 2>&1
usermod -aG audio "$JARVIS_USER"
ok "Audio subsystem installed"

# ── Phase 4: Python environment ────────────────────────────────
info "Phase 4/7: Setting up Python environment..."
mkdir -p /opt/jarvis
python3 -m venv /opt/jarvis/.venv
/opt/jarvis/.venv/bin/pip install --upgrade pip setuptools wheel -q
/opt/jarvis/.venv/bin/pip install -q \
    aiohttp beautifulsoup4 duckduckgo-search \
    edge-tts faster-whisper \
    grpcio grpcio-tools msgpack \
    numpy opencv-python-headless \
    requests rich sounddevice
ok "Python environment ready"

# ── Phase 5: Install JARVIS application ────────────────────────
info "Phase 5/7: Installing JARVIS application..."
mkdir -p /var/lib/jarvis/{data,evolved,logs,lattice,models,checkpoints}

# Copy source
if [ -d "$JARVIS_SRC/brain" ]; then
    cp -r "$JARVIS_SRC/brain" /opt/jarvis/
    cp -r "$JARVIS_SRC/shells" /opt/jarvis/
    [ -d "$JARVIS_SRC/proto" ] && cp -r "$JARVIS_SRC/proto" /opt/jarvis/
    [ -f "$JARVIS_SRC/pyproject.toml" ] && cp "$JARVIS_SRC/pyproject.toml" /opt/jarvis/
    [ -f "$JARVIS_SRC/.env" ] && cp "$JARVIS_SRC/.env" /opt/jarvis/.env && chmod 600 /opt/jarvis/.env
    ok "JARVIS source installed to /opt/jarvis/"
else
    fail "JARVIS source not found at $JARVIS_SRC/brain — set JARVIS_SRC"
fi

if [ -d "$COGSCRIPT_SRC/cogscript" ]; then
    cp -r "$COGSCRIPT_SRC" /opt/jarvis/CogScript
    ok "CogScript installed"
else
    info "CogScript not found at $COGSCRIPT_SRC — skipping"
fi

# OS environment file
cat > /opt/jarvis/.env.os <<'EOF'
JARVIS_HOME=/var/lib/jarvis
JARVIS_MODE=service
PYTHONPATH=/opt/jarvis:/opt/jarvis/CogScript
PYTHONUNBUFFERED=1
EOF

chown -R "$JARVIS_USER:$JARVIS_USER" /opt/jarvis /var/lib/jarvis

# ── Phase 6: systemd services ─────────────────────────────────
info "Phase 6/7: Installing systemd services..."
SYSTEMD_DIR="$SCRIPT_DIR/systemd"
if [ -d "$SYSTEMD_DIR" ]; then
    cp "$SYSTEMD_DIR"/*.service /etc/systemd/system/
    cp "$SYSTEMD_DIR"/*.target /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable jarvis.target
    systemctl enable jarvis-memory.service
    systemctl enable jarvis-brain.service
    systemctl enable jarvis-web.service
    systemctl enable jarvis-speech.service
    systemctl enable jarvis-greeting.service
    ok "systemd services installed and enabled"
else
    info "No systemd dir found at $SYSTEMD_DIR — skipping service install"
fi

# ── Phase 7: GUI + Boot configuration ─────────────────────────
info "Phase 7/7: Configuring GUI and boot..."

# Sway config
SWAY_SRC="$SCRIPT_DIR/sway"
mkdir -p /home/$JARVIS_USER/.config/sway
if [ -d "$SWAY_SRC" ]; then
    cp "$SWAY_SRC/config" /home/$JARVIS_USER/.config/sway/config
fi

# Waybar config
WAYBAR_SRC="$SCRIPT_DIR/waybar"
mkdir -p /home/$JARVIS_USER/.config/waybar
if [ -d "$WAYBAR_SRC" ]; then
    cp "$WAYBAR_SRC/config.jsonc" /home/$JARVIS_USER/.config/waybar/config.jsonc 2>/dev/null || true
    cp "$WAYBAR_SRC/style.css" /home/$JARVIS_USER/.config/waybar/style.css 2>/dev/null || true
    [ -f "$WAYBAR_SRC/jarvis-status.sh" ] && install -m 755 "$WAYBAR_SRC/jarvis-status.sh" /usr/local/bin/jarvis-status
fi

# Mako notifications
mkdir -p /home/$JARVIS_USER/.config/mako
cat > /home/$JARVIS_USER/.config/mako/config <<'EOF'
default-timeout=5000
background-color=#0a0a1aee
text-color=#4a8abaff
border-color=#2a5a8aff
border-size=2
border-radius=8
font=Fira Code 11
width=400
EOF

# Auto-login on tty1
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $JARVIS_USER --noclear %I \$TERM
EOF

# Auto-start Sway on login
cat >> /home/$JARVIS_USER/.bash_profile <<'PROFILE'

# JARVIS OS — Auto-start Sway on tty1
if [ "$(tty)" = "/dev/tty1" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    export XDG_SESSION_TYPE=wayland
    export XDG_CURRENT_DESKTOP=sway
    export MOZ_ENABLE_WAYLAND=1
    exec sway 2>/tmp/sway.log
fi
PROFILE

# Boot branding
cat > /etc/motd <<'EOF'

     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝

    Autonomous Intelligence Operating System
    All systems nominal.

EOF

echo "jarvis" > /etc/hostname
systemctl set-default graphical.target

# VirtualBox guest additions (if available)
apt-get install -y -qq virtualbox-guest-utils 2>/dev/null || true
groupadd -f vboxsf 2>/dev/null || true
usermod -aG vboxsf "$JARVIS_USER" 2>/dev/null || true

chown -R "$JARVIS_USER:$JARVIS_USER" /home/$JARVIS_USER/.config /home/$JARVIS_USER/.bash_profile

ok "GUI and boot configured"

# ── Done ───────────────────────────────────────────────────────
echo
echo "╔══════════════════════════════════════════════╗"
echo "║  JARVIS OS Setup Complete!                    ║"
echo "╠══════════════════════════════════════════════╣"
echo "║                                              ║"
echo "║  Reboot to start JARVIS OS:                  ║"
echo "║    sudo reboot                               ║"
echo "║                                              ║"
echo "║  Boot sequence:                              ║"
echo "║    1. Auto-login as '$JARVIS_USER'           ║"
echo "║    2. Sway starts automatically              ║"
echo "║    3. Chromium kiosk → arc reactor UI        ║"
echo "║    4. Voice greeting plays                   ║"
echo "║                                              ║"
echo "║  Web UI: http://localhost:8765               ║"
echo "║  User: $JARVIS_USER / jarvis                 ║"
echo "║                                              ║"
echo "╚══════════════════════════════════════════════╝"
echo
