# Misty Scone — VM Baseline Runbook

End-to-end: from nothing to a snapshotted Omarchy + BlackArch + full Kali-equivalent pentest VM.

## Prerequisites (host)

- VMware Workstation Pro 17+.
- `vmrun` on `$PATH` (Workstation installs it to `/usr/bin/vmrun` on Linux).
- Arch ISO (x86_64) from https://archlinux.org/download — pick latest monthly.
- 80+ GB free disk.

## Step 1: Create the VM (VMware Workstation UI)

1. `File → New Virtual Machine → Custom (advanced)`.
2. Hardware compatibility: 17.x (or latest).
3. Install OS later.
4. Guest OS: Linux → Other Linux 6.x kernel 64-bit.
5. Name: `misty-base`. Location: `~/vmware/misty-base/`.
6. Processors: 4 cores, 1 socket.
7. Memory: 8192 MB.
8. Network: Bridged (so the VM can see LAN targets).
9. I/O controller: LSI Logic. Disk type: SCSI. Size: 64 GB, single file.
10. Finish. Edit VM → Display → enable `Accelerate 3D graphics`, graphics memory 2 GB (required for Hyprland/Wayland).
11. Attach the Arch ISO to the CD/DVD drive.

## Step 2: Install Arch Linux

Use `archinstall` (the guided installer on the ISO). Choices:
- Keyboard: your layout
- Locale / mirrors: your region
- Disk: use /dev/sda, single-disk guided, `ext4`, no swap (8 GB RAM is fine)
- Hostname: `misty`
- Root password: set
- User: `ulrich`, password, member of `wheel`
- Profile: Minimal
- Audio: pipewire
- Network: NetworkManager
- Additional packages: `base-devel git`
- Bootloader: systemd-boot

Reboot. Log in as `ulrich`.

## Step 3: Install Omarchy

Per https://omarchy.org install instructions (summary — verify current command at that URL):

```bash
bash <(curl -fsSL https://omarchy.org/install)
```

Reboot. You should boot into Hyprland with Omarchy's config.

## Step 4: Clone this repo into the VM

```bash
# Inside the VM — replace <you> with your GitHub username (or use the shared-folder path below).
sudo pacman -S --noconfirm git
git clone https://github.com/<you>/jarvis.git ~/jarvis
cd ~/jarvis
```

Alternative: configure a VMware shared folder from the host's jarvis repo — no clone needed. Before running `01-blackarch.sh`, verify the strap.sh SHA1 pinned in that script still matches the current value at https://blackarch.org/downloads.html — upstream rotates this occasionally.

## Step 5: Run the layered install scripts in order

```bash
cd ~/jarvis/src/os/desktop/scripts/install

./00-preflight.sh          # ensure Arch + Omarchy
./01-blackarch.sh          # layer BlackArch repo (may take a few min for keyring import)
./02-pentools.sh           # install the ~50 Kali-equivalent packages (this is the slow one: 20-40 min, several GB)
./03-postinstall.sh        # msfdb, wpscan, wireshark group, etc.
```

If `02-pentools.sh` reports a few failed packages, the script prints them (it exits with code 2 for partial failure, which is a soft error — continue on); decide whether to install manually or skip. Transient BlackArch breakage is normal — try again later.

## Step 6: Snapshot from the host

```bash
# On the host
cd ~/jarvis/src/os/desktop/scripts/vm
cp vm-config.env.example vm-config.env
$EDITOR vm-config.env      # set VMX_PATH to ~/vmware/misty-base/misty-base.vmx
./snapshot.sh base
```

Verify: `./list.sh` should show `base`.

## Step 7: Verification (inside VM)

Sanity-check tools are present:

```bash
for bin in nmap masscan burpsuite msfconsole sqlmap hydra john hashcat aircrack-ng tshark zaproxy gobuster ffuf wpscan nikto; do
  if command -v "$bin" >/dev/null 2>&1; then
    printf '[ok]   %s\n' "$bin"
  else
    printf '[miss] %s\n' "$bin"
  fi
done
```

Expect all `[ok]`.

End-to-end sanity: `nmap 127.0.0.1` — should show open ports quickly.

## Rolling back

```bash
# Host
./restore.sh base
```

This powers off the VM, reverts to the `base` snapshot, and starts it again in the background. **Note:** `restore.sh` backgrounds the `vmrun start` call so your shell returns immediately; the VM takes 10–30 seconds to actually finish booting. Use `./list.sh` or check the VMware UI to confirm the VM is running before running commands that expect it to be up.

## What's next

With `base` snapshotted, downstream plans (misty-core skeleton, Hyprland integration, voice, etc.) all start from this snapshot. When iterating on a downstream plan, if you wreck the VM, `./restore.sh base` gets you back.
