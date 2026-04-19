#!/usr/bin/env bash
# install.sh — install HUD deps + symlink hud/ into ~/.config/eww/misty/
# Run inside the VM after jarvis repo is cloned and misty-core is set up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
EWW_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/eww/misty"

LIB="$SCRIPT_DIR/../scripts/install/lib.sh"
if [[ -r "$LIB" ]]; then
  # shellcheck source=../scripts/install/lib.sh
  # shellcheck disable=SC1091
  source "$LIB"
else
  # Fallback: inline the minimal helpers we need when lib.sh isn't on this branch.
  log()  { printf '[misty] %s\n' "$*"; }
  ok()   { printf '[ok]    %s\n' "$*"; }
  warn() { printf '[warn]  %s\n' "$*" >&2; }
  die()  { printf '[err]   %s\n' "$*" >&2; exit 1; }
  require_bin() { command -v "$1" >/dev/null 2>&1 || die "required binary not found: $1"; }
fi

log "Installing HUD dependencies"
require_bin pacman
for pkg in eww jq curl; do
  if ! pacman -Qi "$pkg" >/dev/null 2>&1; then
    log "  installing $pkg"
    sudo pacman -S --needed --noconfirm "$pkg"
  else
    ok "  $pkg already installed"
  fi
done

log "Linking HUD config to $EWW_CFG"
mkdir -p "$(dirname "$EWW_CFG")"

if [[ -e "$EWW_CFG" && ! -L "$EWW_CFG" ]]; then
  die "$EWW_CFG exists but is not a symlink; refusing to overwrite. Move it aside and re-run."
fi

if [[ -L "$EWW_CFG" ]]; then
  if [[ "$(readlink "$EWW_CFG")" == "$SCRIPT_DIR" ]]; then
    ok "already symlinked — nothing to do"
  else
    warn "$EWW_CFG points elsewhere; replacing"
    rm "$EWW_CFG"
    ln -s "$SCRIPT_DIR" "$EWW_CFG"
    ok "symlink replaced"
  fi
else
  ln -s "$SCRIPT_DIR" "$EWW_CFG"
  ok "symlinked $EWW_CFG → $SCRIPT_DIR"
fi

log "HUD install complete"
log "Start: eww -c \"$EWW_CFG\" daemon && eww -c \"$EWW_CFG\" open misty-hud"
