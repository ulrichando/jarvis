#!/bin/bash
# JARVIS OS — GUI configuration (Sway + Waybar)
set -euo pipefail

echo "[JARVIS OS] Configuring GUI..."

# Install Sway config
mkdir -p /home/jarvis/.config/sway
if [ -d /tmp/jarvis-sway ]; then
    cp /tmp/jarvis-sway/config /home/jarvis/.config/sway/config
    [ -f /tmp/jarvis-sway/jarvis-start.sh ] && \
        install -m 755 /tmp/jarvis-sway/jarvis-start.sh /usr/local/bin/jarvis-start
fi

# Install Waybar config
mkdir -p /home/jarvis/.config/waybar
if [ -d /tmp/jarvis-waybar ]; then
    cp /tmp/jarvis-waybar/config.jsonc /home/jarvis/.config/waybar/config.jsonc 2>/dev/null || true
    cp /tmp/jarvis-waybar/style.css /home/jarvis/.config/waybar/style.css 2>/dev/null || true
    [ -f /tmp/jarvis-waybar/jarvis-status.sh ] && \
        install -m 755 /tmp/jarvis-waybar/jarvis-status.sh /usr/local/bin/jarvis-status
fi

# Auto-login jarvis user on tty1
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin jarvis --noclear %I $TERM
EOF

# Start Sway on login (add to .bash_profile)
cat >> /home/jarvis/.bash_profile <<'PROFILE'

# Auto-start Sway on tty1
if [ "$(tty)" = "/dev/tty1" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    export XDG_SESSION_TYPE=wayland
    export XDG_CURRENT_DESKTOP=sway
    export MOZ_ENABLE_WAYLAND=1
    exec sway 2>/tmp/sway.log
fi
PROFILE

# Set wallpaper fallback — solid dark background if no image
mkdir -p /opt/jarvis/os/assets
if [ ! -f /opt/jarvis/os/assets/wallpaper.png ]; then
    # Generate a simple dark wallpaper with JARVIS branding
    convert -size 1920x1080 xc:'#0a0a1a' \
        -fill '#1a3a5a' -draw "circle 960,540 960,400" \
        -fill '#2a5a8a' -draw "circle 960,540 960,460" \
        -fill '#0a0a1a' -draw "circle 960,540 960,480" \
        -fill '#4a8aba' -font Noto-Sans -pointsize 36 \
        -gravity center -annotate +0+200 "J.A.R.V.I.S." \
        /opt/jarvis/os/assets/wallpaper.png 2>/dev/null || \
    # Fallback: just a solid color PNG (1x1 pixel, Sway will fill)
    printf '\x89PNG\r\n\x1a\n' > /opt/jarvis/os/assets/wallpaper.png || true
fi

# Mako notification daemon config
mkdir -p /home/jarvis/.config/mako
cat > /home/jarvis/.config/mako/config <<'EOF'
default-timeout=5000
background-color=#0a0a1aee
text-color=#4a8abaff
border-color=#2a5a8aff
border-size=2
border-radius=8
font=Fira Code 11
width=400
EOF

chown -R jarvis:jarvis /home/jarvis/.config /home/jarvis/.bash_profile

echo "[JARVIS OS] GUI configured."
