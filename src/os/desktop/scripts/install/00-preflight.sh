#!/usr/bin/env bash
# 00-preflight.sh — sanity-check we're inside an Arch VM with Omarchy present.
# Run first, before 01-blackarch.sh and 02-pentools.sh.
# Exits non-zero on any mismatch.

set -euo pipefail
# shellcheck source=lib.sh
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/lib.sh"

log "preflight: verifying environment"

require_arch
ok "OS is Arch (or derivative)"

require_bin pacman
require_bin sudo
require_bin curl
ok "core binaries present"

# Omarchy/Hyprland is the intended desktop but not strictly required for the
# pentools install layer. Set MISTY_SKIP_HYPRLAND_CHECK=1 to run on minimal Arch
# (e.g. headless/VM workflows where Hyprland isn't appropriate).
if command -v Hyprland >/dev/null 2>&1; then
  ok "Hyprland present"
elif [[ -d "$HOME/.local/share/omarchy" ]] || [[ -d /opt/omarchy ]]; then
  if [[ -n "${MISTY_SKIP_HYPRLAND_CHECK:-}" ]]; then
    warn "Omarchy dir present but Hyprland binary missing; continuing per MISTY_SKIP_HYPRLAND_CHECK"
  else
    die "Omarchy dir present but Hyprland binary missing — install looks incomplete; re-run the Omarchy bootstrap"
  fi
else
  if [[ -n "${MISTY_SKIP_HYPRLAND_CHECK:-}" ]]; then
    warn "no Hyprland/Omarchy found; continuing per MISTY_SKIP_HYPRLAND_CHECK (minimal-Arch mode)"
  else
    die "neither Hyprland nor an Omarchy install dir found — install Omarchy first (or set MISTY_SKIP_HYPRLAND_CHECK=1)"
  fi
fi

# Refuse to run as root; the install scripts use sudo internally.
[[ $EUID -ne 0 ]] || die "run as a regular user; scripts invoke sudo themselves"
ok "running as non-root user $(id -un)"

# Warn on low disk: Kali-equivalent toolset + metasploit + ghidra is ~8 GB.
avail_kb=$(df -Pk / | awk 'NR==2 {print $4}') || die "df failed — cannot check disk space"
avail_gb=$((avail_kb / 1024 / 1024))
if (( avail_gb < 20 )); then
  die "low disk: only ${avail_gb}G free on /; need 20G+ headroom"
fi
ok "disk headroom OK (${avail_gb}G free)"

log "preflight complete"
