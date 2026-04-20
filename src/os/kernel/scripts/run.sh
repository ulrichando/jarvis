#!/usr/bin/env bash
# Build + run the JARVIS kernel in QEMU.
#
# First time only:
#   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
#   cargo install bootimage
#   sudo pacman -S qemu-full       # or: apt install qemu-system-x86
#
# Thereafter:
#   ./scripts/run.sh           → boots the kernel in a QEMU window
#   ./scripts/run.sh --nographic → boots the kernel, VGA relayed over serial
#
# The runner (in .cargo/config.toml) wraps `cargo run` around bootimage + qemu.

set -euo pipefail
cd "$(dirname "$0")/.."

# Ensure bootimage is installed.
if ! command -v bootimage >/dev/null 2>&1; then
  echo "bootimage not found — install it with:"
  echo "    cargo install bootimage"
  exit 1
fi

if [[ "${1:-}" == "--nographic" ]]; then
  export QEMU_ARGS="-nographic"
fi

exec cargo run --release
