#!/usr/bin/env bash
# 01-blackarch.sh — add BlackArch repo to this Arch install, idempotent.
# Prereq: 00-preflight.sh passed.

set -euo pipefail
# shellcheck source=lib.sh
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/lib.sh"

require_arch
require_bin pacman
require_bin curl
require_bin sha1sum

readonly STRAP_URL='https://blackarch.org/strap.sh'
# Pinned SHA1. Rotated 2026-04-19 after live VM dry-run caught upstream rotation.
readonly STRAP_SHA1='00688950aaf5e5804d2abebb8d3d3ea1d28525ed'

log "checking if BlackArch repo is already configured"
if grep -Eq '^\s*\[blackarch\]' /etc/pacman.conf 2>/dev/null; then
  log "[blackarch] already in /etc/pacman.conf; running pacman -Syy"
  sudo pacman -Syy --noconfirm
  ok "BlackArch repo already configured; pacman DB synced"
  exit 0
fi

log "fetching strap.sh"
tmp=$(mktemp -d)
trap '[[ -n "${tmp:-}" ]] && rm -rf "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL "$STRAP_URL" -o "$tmp/strap.sh"

log "verifying SHA1"
actual=$(sha1sum "$tmp/strap.sh" | awk '{print $1}')
if [[ "$actual" != "$STRAP_SHA1" ]]; then
  die "strap.sh SHA1 mismatch: expected $STRAP_SHA1 got $actual — upstream may have rotated; update the pin in this script after verifying manually"
fi
ok "SHA1 verified"

log "running strap.sh (installs BlackArch keyring, appends repo)"
chmod +x "$tmp/strap.sh"
sudo "$tmp/strap.sh"

log "syncing pacman databases"
sudo pacman -Syy --noconfirm

ok "BlackArch repo ready"
