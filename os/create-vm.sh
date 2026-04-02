#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  JARVIS OS — Create VirtualBox VM                           ║
# ║                                                              ║
# ║  Creates a Debian VM, boots it, and runs the setup script    ║
# ║  to transform it into JARVIS OS.                             ║
# ║                                                              ║
# ║  Prerequisites:                                              ║
# ║    - VirtualBox installed                                    ║
# ║    - Debian netinst ISO downloaded                           ║
# ║                                                              ║
# ║  Usage: bash create-vm.sh [path-to-debian.iso]               ║
# ╚══════════════════════════════════════════════════════════════╝
set -euo pipefail

VM_NAME="JARVIS-OS"
VM_RAM=4096
VM_CPUS=4
VM_DISK=40000  # 40GB
VM_VRAM=128

ISO="${1:-}"

if [ -z "$ISO" ]; then
    echo "Usage: bash create-vm.sh /path/to/debian-13-amd64-netinst.iso"
    echo
    echo "Download from: https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"
    echo
    echo "After Debian is installed in the VM:"
    echo "  1. Copy jarvis/ and CogScript/ into the VM"
    echo "  2. Run: sudo bash /path/to/jarvis/os/setup-jarvis-os.sh"
    echo "  3. Reboot"
    exit 1
fi

[ -f "$ISO" ] || { echo "ISO not found: $ISO"; exit 1; }

echo "╔══════════════════════════════════════════════╗"
echo "║  Creating JARVIS OS VirtualBox VM            ║"
echo "╚══════════════════════════════════════════════╝"
echo

# Remove existing VM
VBoxManage unregistervm "$VM_NAME" --delete 2>/dev/null || true

# Create VM
echo "[1/5] Creating VM..."
VBoxManage createvm --name "$VM_NAME" --ostype Debian_64 --register

# Configure
echo "[2/5] Configuring VM..."
VBoxManage modifyvm "$VM_NAME" \
    --memory $VM_RAM \
    --cpus $VM_CPUS \
    --vram $VM_VRAM \
    --graphicscontroller vmsvga \
    --audio-driver pulse \
    --audio-enabled on \
    --audio-out on \
    --audio-in on \
    --clipboard-mode bidirectional \
    --draganddrop bidirectional \
    --nic1 nat \
    --natpf1 "jarvis-web,tcp,,8765,,8765" \
    --natpf1 "ssh,tcp,,2222,,22"

# Create disk
echo "[3/5] Creating disk..."
DISK_PATH="$HOME/VirtualBox VMs/$VM_NAME/$VM_NAME.vdi"
VBoxManage createmedium disk --filename "$DISK_PATH" --size $VM_DISK --format VDI

# Storage controllers
echo "[4/5] Attaching storage..."
VBoxManage storagectl "$VM_NAME" --name "SATA" --add sata --controller IntelAhci
VBoxManage storageattach "$VM_NAME" --storagectl "SATA" --port 0 --device 0 --type hdd --medium "$DISK_PATH"
VBoxManage storagectl "$VM_NAME" --name "IDE" --add ide
VBoxManage storageattach "$VM_NAME" --storagectl "IDE" --port 0 --device 0 --type dvddrive --medium "$ISO"

# Boot order: disk first, then DVD
VBoxManage modifyvm "$VM_NAME" --boot1 dvd --boot2 disk --boot3 none --boot4 none

echo "[5/5] Starting VM..."
VBoxManage startvm "$VM_NAME" --type gui

echo
echo "╔══════════════════════════════════════════════╗"
echo "║  VM is booting the Debian installer.         ║"
echo "║                                              ║"
echo "║  After Debian installs:                      ║"
echo "║    1. SSH into the VM:                       ║"
echo "║       ssh -p 2222 jarvis@localhost            ║"
echo "║                                              ║"
echo "║    2. Copy JARVIS source:                    ║"
echo "║       scp -P 2222 -r jarvis/ jarvis@localhost:~/ ║"
echo "║       scp -P 2222 -r CogScript/ jarvis@localhost:~/ ║"
echo "║                                              ║"
echo "║    3. Run setup:                             ║"
echo "║       sudo bash ~/jarvis/os/setup-jarvis-os.sh ║"
echo "║                                              ║"
echo "║    4. Reboot:                                ║"
echo "║       sudo reboot                            ║"
echo "║                                              ║"
echo "║  JARVIS OS will boot with the arc reactor UI ║"
echo "║                                              ║"
echo "║  Keybindings:                                ║"
echo "║    Mod+1  = Web UI (arc reactor)             ║"
echo "║    Mod+J  = JARVIS CLI (agent mode)          ║"
echo "║    Mod+T  = JARVIS Terminal (REPL)           ║"
echo "║    Mod+Return = System terminal              ║"
echo "║    Mod+Shift+E = Shutdown menu               ║"
echo "╚══════════════════════════════════════════════╝"
