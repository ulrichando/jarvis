#!/bin/bash
# JARVIS OS — Install JARVIS application
set -euo pipefail

echo "[JARVIS OS] Installing JARVIS application..."

# Create data directories
mkdir -p /var/lib/jarvis/{data,evolved,logs,lattice,models,checkpoints}

# Copy JARVIS source
if [ -d /tmp/jarvis-src ]; then
    # Copy brain, shells, proto
    cp -r /tmp/jarvis-src/brain /opt/jarvis/
    cp -r /tmp/jarvis-src/shells /opt/jarvis/
    cp -r /tmp/jarvis-src/proto /opt/jarvis/

    # Copy config files
    [ -f /tmp/jarvis-src/pyproject.toml ] && cp /tmp/jarvis-src/pyproject.toml /opt/jarvis/
    [ -f /tmp/jarvis-src/.env ] && cp /tmp/jarvis-src/.env /opt/jarvis/.env

    echo "[JARVIS OS] JARVIS source installed to /opt/jarvis/"
else
    echo "[JARVIS OS] WARNING: No jarvis source at /tmp/jarvis-src"
fi

# Copy CogScript
if [ -d /tmp/cogscript-src ]; then
    cp -r /tmp/cogscript-src /opt/jarvis/CogScript
    echo "[JARVIS OS] CogScript installed to /opt/jarvis/CogScript/"
else
    echo "[JARVIS OS] WARNING: No CogScript source at /tmp/cogscript-src"
fi

# Copy OS assets
if [ -d /tmp/jarvis-assets ]; then
    mkdir -p /opt/jarvis/os/assets
    cp -r /tmp/jarvis-assets/* /opt/jarvis/os/assets/
fi

# Set up environment file
cat > /opt/jarvis/.env.os <<'EOF'
# JARVIS OS Environment
JARVIS_HOME=/var/lib/jarvis
JARVIS_MODE=service
PYTHONPATH=/opt/jarvis:/opt/jarvis/CogScript
PYTHONUNBUFFERED=1
EOF

# Set ownership
chown -R jarvis:jarvis /opt/jarvis /var/lib/jarvis
chmod 600 /opt/jarvis/.env 2>/dev/null || true

echo "[JARVIS OS] Application installation complete."
