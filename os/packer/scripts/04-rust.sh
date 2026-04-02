#!/bin/bash
# JARVIS OS — Rust toolchain and gRPC core build
set -euo pipefail

echo "[JARVIS OS] Installing Rust toolchain..."

# Install Rust as jarvis user
su - jarvis -c 'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y'

# Build the JARVIS gRPC core server
echo "[JARVIS OS] Building JARVIS core..."
mkdir -p /opt/jarvis/bin

if [ -d /tmp/jarvis-src/core ]; then
    su - jarvis -c "
        source ~/.cargo/env
        cd /tmp/jarvis-src
        cargo build --release -p jarvis-core 2>&1 || echo 'Rust build skipped (may not have all deps)'
    "

    if [ -f /tmp/jarvis-src/target/release/jarvis-core ]; then
        cp /tmp/jarvis-src/target/release/jarvis-core /opt/jarvis/bin/
        chmod +x /opt/jarvis/bin/jarvis-core
        echo "[JARVIS OS] JARVIS core binary installed."
    else
        echo "[JARVIS OS] JARVIS core binary not found — gRPC service will be unavailable."
    fi
else
    echo "[JARVIS OS] No jarvis source found at /tmp/jarvis-src — skipping Rust build."
fi

echo "[JARVIS OS] Rust setup complete."
