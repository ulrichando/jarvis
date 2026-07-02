#!/usr/bin/env bash
# JARVIS uninstaller — CLI · Voice Agent · Desktop · Web (local machine).
#
# Usage:
#   ./uninstall.sh                  remove everything (SOFTWARE only — keeps data + repo)
#   ./uninstall.sh --voice          remove only the voice agent + its services
#   ./uninstall.sh --cli --desktop  remove specific channels (the rest stay)
#   ./uninstall.sh --purge          ALSO wipe user data (~/.jarvis, ~/.local/share/jarvis)
#   ./uninstall.sh --nuke           ALSO delete the repo clone (refuses if uncommitted; --force overrides)
#   ./uninstall.sh --all --purge -y full data wipe, no prompts
#
# Channels: --cli --voice --desktop --web --all   (default: --all)
# Scope:    software (default) | --purge (+ user data) | --nuke (+ repo)
# Flags:    -y/--yes (no prompt) · --force (override the --nuke dirty-tree guard)
#
# Safe by default: your memories, keys, conversations and the repo are KEPT
# unless you pass --purge / --nuke. Symmetric with install.sh.

# NOT set -e: an uninstaller must continue past individual failures so a single
# missing artifact never leaves the machine half-cleaned.
set -uo pipefail

readonly DEFAULT_INSTALL_DIR="$HOME/Documents/Projects/jarvis"
readonly LOCAL_BIN="$HOME/.local/bin"
readonly USER_SYSTEMD="$HOME/.config/systemd/user"
INSTALL_DIR="${JARVIS_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

# ── Output helpers (match install.sh) ─────────────────────────────────────
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
have()     { command -v "$1" >/dev/null 2>&1; }
# Remove a path (file/dir/symlink) only if it exists, with an accurate message.
rm_if()    { if [ -e "$1" ] || [ -L "$1" ]; then rm -rf "$1" && ok "removed ${2:-$1}"; fi; }

usage() {
  cat <<'EOF'
JARVIS uninstaller — CLI · Voice Agent · Desktop · Web (local machine).

Usage:
  ./uninstall.sh                  remove everything (SOFTWARE only — keeps data + repo)
  ./uninstall.sh --voice          remove only the voice agent + its services
  ./uninstall.sh --cli --desktop  remove specific channels (the rest stay)
  ./uninstall.sh --purge          ALSO wipe user data (~/.jarvis, ~/.local/share/jarvis)
  ./uninstall.sh --nuke           ALSO delete the repo clone (refuses if uncommitted; --force)
  ./uninstall.sh --all --purge -y full data wipe, no prompts

Channels: --cli --voice --desktop --web --all   (default: --all)
Scope:    software (default) | --purge (+ user data) | --nuke (+ repo)
Flags:    -y/--yes (no prompt) · --force (override the --nuke dirty-tree guard)

Safe by default: memories, keys, conversations and the repo are KEPT unless you
pass --purge / --nuke.
EOF
}

# ── Detect the checkout (sibling CLAUDE.md), else the default/env dir ──────
detect_install_dir() {
  local d
  if [ -f "${BASH_SOURCE[0]:-/dev/null}" ]; then
    d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$d/CLAUDE.md" ] && grep -q "^# JARVIS" "$d/CLAUDE.md" 2>/dev/null; then
      INSTALL_DIR="$d"
    fi
  fi
}

# ── Channel: CLI ──────────────────────────────────────────────────────────
uninstall_cli() {
  section "Removing CLI"
  rm_if "$LOCAL_BIN/jarvis" "$LOCAL_BIN/jarvis symlink"
  sub "(repo + ~/.jarvis config preserved — use --purge to remove data)"
}

# ── Channel: Voice Agent + all always-on services ─────────────────────────
# The systemd units (voice-agent, voice-client, livekit-server, proxy, and the
# backup/health/dep/evolution/cron timers) are the always-on JARVIS daemons,
# anchored by the voice agent — so they're removed with --voice (or --all).
uninstall_voice() {
  section "Removing Voice Agent + services"
  if have systemctl; then
    local units
    units="$(systemctl --user list-unit-files --no-legend 'jarvis-*' 'livekit-server*' 2>/dev/null | awk '{print $1}')"
    for u in $units; do systemctl --user disable --now "$u" >/dev/null 2>&1 || true; done
    systemctl --user stop 'jarvis-*' 'livekit-server*' >/dev/null 2>&1 || true
    # unit files + drop-in dirs (.service.d) + .bak leftovers
    find "$USER_SYSTEMD" -maxdepth 1 \( -name 'jarvis-*' -o -name 'livekit-server*' \) -exec rm -rf {} + 2>/dev/null || true
    systemctl --user daemon-reload  >/dev/null 2>&1 || true
    systemctl --user reset-failed   >/dev/null 2>&1 || true
    ok "stopped + removed systemd units"
  else
    warn "no systemctl — skipping unit removal"
  fi
  rm_if "$INSTALL_DIR/src/voice-agent/.venv" "voice-agent .venv"
  if have docker; then
    docker rm -f kokoro-tts kokoro-fastapi >/dev/null 2>&1 && ok "removed kokoro TTS containers" || true
    if [ -f "$HOME/honcho/docker-compose.yml" ]; then
      (cd "$HOME/honcho" && docker compose down >/dev/null 2>&1) && ok "stopped honcho memory stack" || true
    fi
  fi
}

# ── Channel: Desktop (Tauri) ──────────────────────────────────────────────
uninstall_desktop() {
  section "Removing Desktop"
  rm_if "$LOCAL_BIN/jarvis-desktop" "$LOCAL_BIN/jarvis-desktop symlink"
  rm_if "$HOME/.local/share/applications/jarvis.desktop" "app-menu entry"
  rm_if "$HOME/.config/autostart/jarvis.desktop" "autostart entry"
  rm_if "$HOME/.jarvis/desktop.env" "desktop.env override"
  rm_if "$INSTALL_DIR/src/voice-agent/desktop-tauri/src-tauri/target" "Tauri build (target/)"
  have update-desktop-database && update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
}

# ── Channel: Web (local dev/fallback) ─────────────────────────────────────
uninstall_web() {
  section "Removing Web (local)"
  rm_if "$INSTALL_DIR/src/web/node_modules" "web node_modules"
  rm_if "$INSTALL_DIR/src/web/.next" "web .next build"
}

# ── --purge: user data ────────────────────────────────────────────────────
purge_data() {
  section "Purging user data (--purge)"
  if have docker && [ -f "$HOME/honcho/docker-compose.yml" ]; then
    (cd "$HOME/honcho" && docker compose down -v >/dev/null 2>&1) || true
  fi
  rm_if "$HOME/.jarvis" "~/.jarvis (keys, memories, conversations, models)"
  rm_if "$HOME/.local/share/jarvis" "~/.local/share/jarvis (telemetry, logs)"
  rm_if "$HOME/honcho" "~/honcho (memory backend + data)"
}

# ── --nuke: the repo clone (guarded) ──────────────────────────────────────
nuke_repo() {
  section "Removing repo (--nuke)"
  if git -C "$INSTALL_DIR" rev-parse >/dev/null 2>&1; then
    if [ -n "$(git -C "$INSTALL_DIR" status --porcelain 2>/dev/null)" ] && [ "$FORCE" != "1" ]; then
      die "repo has UNCOMMITTED changes — refusing --nuke without --force: $INSTALL_DIR"
    fi
  fi
  cd "$HOME" 2>/dev/null || true
  rm_if "$INSTALL_DIR" "repo $INSTALL_DIR"
}

# ── Args ──────────────────────────────────────────────────────────────────
ALL=0 SEL=0 DO_CLI=0 DO_VOICE=0 DO_DESKTOP=0 DO_WEB=0 PURGE=0 NUKE=0 FORCE=0 YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --all)     ALL=1 ;;
    --cli)     DO_CLI=1; SEL=1 ;;
    --voice)   DO_VOICE=1; SEL=1 ;;
    --desktop) DO_DESKTOP=1; SEL=1 ;;
    --web)     DO_WEB=1; SEL=1 ;;
    --purge)   PURGE=1 ;;
    --nuke)    NUKE=1 ;;
    --force)   FORCE=1 ;;
    -y|--yes)  YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) warn "ignoring unknown option: $1" ;;
  esac
  shift
done
# No channel selected, or --all → everything.
if [ "$SEL" = "0" ] || [ "$ALL" = "1" ]; then DO_CLI=1; DO_VOICE=1; DO_DESKTOP=1; DO_WEB=1; fi

detect_install_dir

# Pre-flight: --nuke on a dirty tree must fail BEFORE we remove any software,
# not after (otherwise the box is half-cleaned then refused). nuke_repo keeps a
# redundant check as belt-and-suspenders.
if [ "$NUKE" = "1" ] && [ "$FORCE" != "1" ] \
   && git -C "$INSTALL_DIR" rev-parse >/dev/null 2>&1 \
   && [ -n "$(git -C "$INSTALL_DIR" status --porcelain 2>/dev/null)" ]; then
  die "repo has UNCOMMITTED changes — refusing --nuke without --force: $INSTALL_DIR"
fi

# ── Plan + confirm ────────────────────────────────────────────────────────
c_bold "JARVIS uninstaller"
sub "Install dir: $INSTALL_DIR"
plan=""
[ "$DO_CLI" = "1" ]     && plan="$plan CLI"
[ "$DO_VOICE" = "1" ]   && plan="$plan Voice"
[ "$DO_DESKTOP" = "1" ] && plan="$plan Desktop"
[ "$DO_WEB" = "1" ]     && plan="$plan Web"
sub "Channels:${plan}"
scope="software only (data + repo kept)"
[ "$PURGE" = "1" ] && scope="software + USER DATA (--purge)"
[ "$NUKE" = "1" ]  && scope="${scope} + REPO (--nuke)"
sub "Scope: $scope"
if [ "$YES" != "1" ]; then
  printf "\nProceed? [y/N] "
  read -r ans || ans=""
  case "${ans:-}" in [Yy]|[Yy][Ee][Ss]) : ;; *) die "aborted — nothing removed" ;; esac
fi

# ── Execute (reverse of install order; data + repo last) ───────────────────
[ "$DO_WEB" = "1" ]     && uninstall_web
[ "$DO_DESKTOP" = "1" ] && uninstall_desktop
[ "$DO_VOICE" = "1" ]   && uninstall_voice
[ "$DO_CLI" = "1" ]     && uninstall_cli
[ "$PURGE" = "1" ]      && purge_data
[ "$NUKE" = "1" ]       && nuke_repo

section "Done"
[ "$NUKE" != "1" ] && sub "Reinstall anytime:  ./install.sh   (or curl … | bash)"
