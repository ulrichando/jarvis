#!/usr/bin/env bash
# 02-pentools.sh — install Kali-equivalent tool set from packages.txt.
# Idempotent. Reports per-package success/failure.
# Prereq: 01-blackarch.sh completed.
# Exit codes: 0 = all packages installed; 2 = some packages failed (soft error, caller should continue). Non-zero exits other than 2 indicate hard errors.

set -euo pipefail
# shellcheck source=lib.sh
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/lib.sh"

require_arch
require_bin pacman

PKG_FILE="$(dirname "$(readlink -f "$0")")/packages.txt"
readonly PKG_FILE
[[ -r "$PKG_FILE" ]] || die "packages.txt not found at $PKG_FILE"

# Extract non-comment, non-blank tokens (first word of each line).
# Strip UTF-8 BOM (Windows editors) and CR (CRLF line endings) so package names don't carry junk bytes.
mapfile -t pkgs < <(
  sed -e '1s/^\xef\xbb\xbf//' -e 's/\r$//' "$PKG_FILE" \
  | grep -Ev '^\s*(#|$)' \
  | awk '{print $1}'
)
log "packages to install: ${#pkgs[@]}"

# pacman -S with --needed is idempotent. We attempt all packages at once; pacman will
# resolve dependencies together. If that fails, fall back to per-package install to
# surface which specific package is broken upstream (BlackArch occasionally breaks).
log "batch install (pacman will resolve deps across all)"
if sudo pacman -S --needed --noconfirm "${pkgs[@]}"; then
  ok "all packages installed"
  exit 0
fi

warn "batch install failed; retrying per-package to identify the broken one(s)"
failed=()
for p in "${pkgs[@]}"; do
  if sudo pacman -S --needed --noconfirm "$p"; then
    ok "  $p"
  else
    warn "  $p FAILED"
    failed+=("$p")
  fi
done

if (( ${#failed[@]} > 0 )); then
  warn "packages that failed to install: ${failed[*]}"
  warn "skipping, not fatal — rerun this script later or install them manually"
  exit 2
fi

ok "all packages installed (per-package retry path)"
