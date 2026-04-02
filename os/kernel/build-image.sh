#!/bin/bash
# JARVIS Kernel — Build bootable disk image
# Creates a BIOS-bootable raw disk image using the bootloader crate's runner
set -euo pipefail

KERNEL_DIR="$(cd "$(dirname "$0")" && pwd)"
KERNEL_BIN="$KERNEL_DIR/target/x86_64-unknown-none/release/jarvis-kernel"
BIOS_IMG="$KERNEL_DIR/target/jarvis-os.img"

echo "╔══════════════════════════════════════════════╗"
echo "║  JARVIS Kernel — Boot Image Builder          ║"
echo "╚══════════════════════════════════════════════╝"
echo

# Step 1: Build kernel in release mode
echo "[1/3] Building kernel..."
cd "$KERNEL_DIR"
cargo +nightly build --release 2>&1 | grep -E "(Compiling jarvis|Finished|error)" || true

if [ ! -f "$KERNEL_BIN" ]; then
    echo "ERROR: Kernel binary not found at $KERNEL_BIN"
    exit 1
fi
echo "  Kernel: $KERNEL_BIN ($(stat -c%s "$KERNEL_BIN") bytes)"

# Step 2: Build boot image using bootloader crate
# The bootloader v0.11 provides a Rust API, but we can also use
# the bootloader_locator to create images. Since the direct API
# approach has cargo config conflicts, we use a Python script
# that constructs a minimal BIOS boot image.
echo "[2/3] Creating boot image..."

# Use the bootloader's disk image creator
# We need to run this as a separate cargo project without no_std
cd "$KERNEL_DIR/boot"
KERNEL_PATH="$KERNEL_BIN" cargo +nightly run --target x86_64-unknown-linux-gnu 2>&1 | tail -10

echo
echo "[3/3] Done!"
echo "  BIOS image: ${KERNEL_BIN}.bios.img"
echo "  UEFI image: ${KERNEL_BIN}.uefi.img"
echo
echo "Run in QEMU (quick test):"
echo "  qemu-system-x86_64 -drive format=raw,file=${KERNEL_BIN}.bios.img -serial stdio"
echo
echo "Run in VirtualBox:"
echo "  VBoxManage convertfromraw ${KERNEL_BIN}.bios.img jarvis-kernel.vdi --format VDI"
echo "  # Then attach jarvis-kernel.vdi to a VM"
