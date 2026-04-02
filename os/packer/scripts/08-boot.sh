#!/bin/bash
# JARVIS OS — Boot branding (GRUB + Plymouth)
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[JARVIS OS] Configuring boot experience..."

# Install Plymouth
apt-get install -y plymouth plymouth-themes

# Install JARVIS Plymouth theme
if [ -d /tmp/jarvis-plymouth/jarvis-theme ]; then
    mkdir -p /usr/share/plymouth/themes/jarvis
    cp -r /tmp/jarvis-plymouth/jarvis-theme/* /usr/share/plymouth/themes/jarvis/
    plymouth-set-default-theme jarvis 2>/dev/null || true
fi

# Configure GRUB for fast, quiet boot
sed -i 's/GRUB_TIMEOUT=.*/GRUB_TIMEOUT=1/' /etc/default/grub
sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"/' /etc/default/grub

# Custom GRUB theme
if [ -d /tmp/jarvis-grub/theme ]; then
    mkdir -p /boot/grub/themes/jarvis
    cp -r /tmp/jarvis-grub/theme/* /boot/grub/themes/jarvis/
    echo 'GRUB_THEME="/boot/grub/themes/jarvis/theme.txt"' >> /etc/default/grub
fi

# Rebuild GRUB config
update-grub

# Custom MOTD
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

# Set hostname
echo "jarvis" > /etc/hostname

echo "[JARVIS OS] Boot branding configured."
