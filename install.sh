#!/usr/bin/env bash
# JARVIS one-shot installer — bootstrap.
#
# On first run (curl|bash) this bootstrap defines the helpers needed before
# the repo is cloned, clones the repo, then sources setup/install-lib.sh
# from the cloned checkout to call main(). When run from an existing checkout
# the lib is sourced at the top so all functions are available immediately.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | bash
#   cd jarvis && ./install.sh                           # existing checkout
#   ./install.sh --setup                                 # config only
#   ./install.sh --ensure browser                        # targeted dep
#   ./install.sh --postinstall                           # pip-user setup
#
# Skip flags: JARVIS_SKIP_CLI=1 / JARVIS_SKIP_VOICE=1 / JARVIS_SKIP_DESKTOP=1
# Custom dir:   JARVIS_INSTALL_DIR=/path/to/jarvis

set -euo pipefail

# Path defaults — MUST be declared before sourcing the lib, because the
# lib functions reference them and set -u kills the script on any unset
# variable reference. detect_fhs() overrides these; _resolve_paths() fills
# in $HOME-based defaults lazily.
INSTALL_DIR="${JARVIS_INSTALL_DIR:-}"
LOCAL_BIN=""; JARVIS_HOME=""; JARVIS_LOG_DIR=""
JARVIS_DATA_DIR=""; SYSTEMD_DIR=""; SYSTEMD_SCOPE=""; VA_ENV=""

# ── Source the function library (when inside a checkout) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
LIB="$SCRIPT_DIR/setup/install-lib.sh"
[ -f "$LIB" ] && source "$LIB"

# If LIB wasn't found above (curl|bash before clone), these bootstrap-only
# functions handle the clone. After clone, main() re-sources the lib from
# the cloned checkout. Test suite sources install.sh from a checkout, so
# the lib is loaded above — bootstrap functions below must not conflict.

# ── Constants ────────────────────────────────────────────────────────────
readonly REPO_URL="https://github.com/ulrichando/jarvis.git"
readonly NODE_VERSION="22"
INSTALL_DIR="${JARVIS_INSTALL_DIR:-}"

# ── Output helpers (needed before clone) ─────────────────────────────────
c_red()    { printf '\033[31m%s\033[0m\n' "$*" >&2; }
c_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
c_bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
section()  { echo; c_bold "═══ $* ═══"; }
sub()      { printf '  %s\n' "$*"; }
ok()       { c_green "  ✓ $*"; }
warn()     { c_yellow "  ⚠ $*"; }
err()      { c_red   "  ✗ $*"; }
die()      { err "$*"; exit 1; }

# ── Primitives ───────────────────────────────────────────────────────────
have() { command -v "$1" >/dev/null 2>&1; }

# ── Detect invocation context ────────────────────────────────────────────
detect_invocation() {
  local script_dir
  if [ -f "${BASH_SOURCE[0]:-/dev/null}" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$script_dir/CLAUDE.md" ] && grep -q "^# JARVIS" "$script_dir/CLAUDE.md" 2>/dev/null; then
      INSTALL_DIR="$script_dir"
      c_bold "Detected existing checkout at: $INSTALL_DIR"
      return 0
    fi
  fi
  c_bold "Will install JARVIS to: $INSTALL_DIR"
  return 0
}

# ── Platform guard ─────────────────────────────────────────────────────────
case "$(uname -s)" in
  Linux)   ;;  # supported
  Darwin)  die "macOS is not supported. JARVIS targets Linux and Windows only." ;;
  *)       die "Unsupported platform: $(uname -s). JARVIS installers target Linux and Windows only." ;;
esac

# ── Bootstrapping prereq check ─────────────────────────────────────────
# Minimal check before clone. Full check happens in lib's check_prereqs().
_check_prereqs_bootstrap() {
  local missing=()
  for cmd in git curl python3; do
    if have "$cmd"; then ok "$cmd"; else err "$cmd not found"; missing+=("$cmd"); fi
  done
  if have bun; then ok "bun"; else warn "bun not found — will install via curl"; missing+=("bun"); fi
  if have node; then ok "node"; else warn "node not found"; missing+=("node"); fi
  if [ ${#missing[@]} -gt 0 ]; then
    warn "Some prerequisites missing — installing them now if possible."
  fi
}

# ── Clone (or update) ────────────────────────────────────────────────────
clone_or_update() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    section "Updating existing checkout"
    git -C "$INSTALL_DIR" fetch --quiet origin master
    local stash_ref=""
    if ! git -C "$INSTALL_DIR" diff --quiet 2>/dev/null; then
      local stash_name="jarvis-install-autostash-$(date +%Y%m%d-%H%M%S)"
      git -C "$INSTALL_DIR" stash push --include-untracked -m "$stash_name" >/dev/null 2>&1 && stash_ref="stash@{0}"
      sub "stashed local changes as '$stash_name'"
    fi
    git -C "$INSTALL_DIR" pull --ff-only origin master || {
      warn "pull --ff-only failed (merge conflict?); leaving as-is"
      [ -n "$stash_ref" ] && warn "stash preserved — git -C $INSTALL_DIR stash apply"
      return 0
    }
    ok "updated to $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
    [ -n "$stash_ref" ] && sub "stash preserved; run: git stash apply to restore"
  else
    section "Cloning JARVIS"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    ok "cloned to $INSTALL_DIR"
  fi
}

# ── Entry point ──────────────────────────────────────────────────────────
# Guard: only run when executed directly, not when sourced (e.g. by tests).
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then

  detect_invocation

  # For curl|bash: INSTALL_DIR is empty; default if not set by detect_invocation.
  [ -z "$INSTALL_DIR" ] && INSTALL_DIR="${JARVIS_INSTALL_DIR:-$HOME/Documents/Projects/jarvis}"

  # Clone if this is a fresh install or update.
  if [ ! -f "$INSTALL_DIR/setup/install-lib.sh" ]; then
    _check_prereqs_bootstrap
    clone_or_update
  fi

  # Source the function library from the (now present) checkout.
  LIB="$INSTALL_DIR/setup/install-lib.sh"
  if [ -f "$LIB" ]; then
    source "$LIB"
  else
    die "Library not found at $LIB. Clone may have failed."
  fi

  # Hand off to the lib's entry routing — same args we received.
  _entry_route "$@"

fi
