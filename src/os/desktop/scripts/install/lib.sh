#!/usr/bin/env bash
# Shared helpers for src/os/desktop/scripts/install/*.sh
# Source with: source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Callers are expected to `set -euo pipefail` themselves before sourcing this file.

# Load-once guard: re-sourcing is a no-op.
[[ -n "${_MISTY_LIB_LOADED:-}" ]] && return 0
_MISTY_LIB_LOADED=1

# Color logging; falls back to plain if stdout isn't a TTY.
if [[ -t 1 ]]; then
  readonly C_RED='\033[0;31m'
  readonly C_GRN='\033[0;32m'
  readonly C_YLW='\033[0;33m'
  readonly C_BLU='\033[0;34m'
  readonly C_RST='\033[0m'
else
  readonly C_RED='' C_GRN='' C_YLW='' C_BLU='' C_RST=''
fi

log()  { printf '%b[misty]%b %s\n' "$C_BLU" "$C_RST" "$*"; }
ok()   { printf '%b[ok]%b    %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%b[warn]%b  %s\n' "$C_YLW" "$C_RST" "$*" >&2; }
die()  { printf '%b[err]%b   %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

# Require a particular binary on PATH.
require_bin() {
  local bin="$1"
  command -v "$bin" >/dev/null 2>&1 || die "required binary not found: $bin"
}

# Exit unless running on Arch (or derivative). Checks /etc/os-release.
require_arch() {
  [[ -r /etc/os-release ]] || die "/etc/os-release missing; refusing to continue"
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}:${ID_LIKE:-}" in
    arch:*|*:arch*) : ;;
    *) die "expected Arch or Arch-derivative; got ID=${ID:-?} ID_LIKE=${ID_LIKE:-?}" ;;
  esac
}

# Error trap: print where we died. To enable in a script, add:
#   trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR
on_err() {
  local exit_code=$? line=${1:-?} cmd=${2:-?}
  printf '%b[misty:trap]%b exit=%d line=%s cmd=%q\n' "$C_RED" "$C_RST" "$exit_code" "$line" "$cmd" >&2
  exit "$exit_code"
}
