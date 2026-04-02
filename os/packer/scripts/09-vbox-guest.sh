#!/bin/bash
# JARVIS OS — VirtualBox Guest Additions
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[JARVIS OS] Installing VirtualBox Guest Additions..."

# Install from Debian packages (cleaner than ISO method)
apt-get install -y \
    virtualbox-guest-utils \
    virtualbox-guest-x11 2>/dev/null || \
apt-get install -y \
    virtualbox-guest-utils 2>/dev/null || \
echo "[JARVIS OS] VirtualBox guest packages not in repo — will use GA ISO if mounted"

# Add jarvis to vboxsf group for shared folders
groupadd -f vboxsf 2>/dev/null || true
usermod -aG vboxsf jarvis

# Create shared folder mount point
mkdir -p /home/jarvis/shared
chown jarvis:jarvis /home/jarvis/shared

# Auto-mount shared folder if configured
cat >> /etc/fstab <<'EOF'

# VirtualBox shared folder (uncomment and set name)
# jarvis_shared /home/jarvis/shared vboxsf defaults,uid=1000,gid=1000 0 0
EOF

echo "[JARVIS OS] VirtualBox Guest Additions configured."
