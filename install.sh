#!/usr/bin/env bash
# JARVIS one-shot installer — CLI + Voice Agent + Desktop (Tauri) + Web.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | bash
# Or, if you already cloned the repo:
#   cd jarvis && ./install.sh
#
# Idempotent: re-running skips channels that are already installed.
# Skip a channel: JARVIS_SKIP_CLI=1 / JARVIS_SKIP_VOICE=1 / JARVIS_SKIP_DESKTOP=1 / JARVIS_SKIP_WEB=1
# Custom install dir: JARVIS_INSTALL_DIR=/path/to/repo (default: ~/Documents/Projects/jarvis)

set -euo pipefail

# ── Constants ────────────────────────────────────────────────────────────
readonly REPO_URL="https://github.com/ulrichando/jarvis.git"
readonly DEFAULT_INSTALL_DIR="$HOME/Documents/Projects/jarvis"
readonly LOCAL_BIN="$HOME/.local/bin"
readonly USER_SYSTEMD="$HOME/.config/systemd/user"

INSTALL_DIR="${JARVIS_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

# ── Output helpers ───────────────────────────────────────────────────────
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

# ── Detect: are we piped from curl, or inside an existing checkout? ──────
detect_invocation() {
  # If $0 ends in "install.sh" AND a sibling CLAUDE.md mentions JARVIS,
  # treat the script's dir as the existing checkout.
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

# ── Prerequisites ────────────────────────────────────────────────────────
have() { command -v "$1" >/dev/null 2>&1; }

check_prereqs() {
  section "Checking prerequisites"

  local missing=()
  for cmd in git curl python3; do
    if have "$cmd"; then ok "$cmd"; else err "$cmd not found"; missing+=("$cmd"); fi
  done

  # Bun — install via official script if missing
  if have bun; then
    ok "bun ($(bun --version))"
  else
    warn "bun not found — install with: curl -fsSL https://bun.sh/install | bash"
    missing+=("bun")
  fi

  # Node + npm — voice/web/desktop need them
  if have node; then ok "node ($(node --version))"; else err "node not found"; missing+=("node"); fi
  if have npm;  then ok "npm  ($(npm --version))";  else err "npm not found";  missing+=("npm"); fi

  # Rust — only required for desktop channel
  if [ "${JARVIS_SKIP_DESKTOP:-0}" != "1" ]; then
    if have cargo; then
      ok "cargo ($(cargo --version | awk '{print $2}'))"
    else
      warn "cargo not found — install rustup with: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
      warn "(or run with JARVIS_SKIP_DESKTOP=1 to skip the desktop build)"
      missing+=("cargo")
    fi
  fi

  # systemd --user
  if have systemctl && systemctl --user --quiet is-system-running 2>/dev/null || [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    ok "systemd --user available"
  else
    warn "systemd --user not detected; voice-agent service won't be auto-enabled"
  fi

  if [ ${#missing[@]} -gt 0 ]; then
    err "Missing: ${missing[*]} — install them and rerun this script."
    exit 1
  fi
}

# ── Clone (or update) ────────────────────────────────────────────────────
clone_or_update() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    section "Updating existing checkout"
    git -C "$INSTALL_DIR" fetch --quiet origin master
    git -C "$INSTALL_DIR" pull --ff-only origin master || warn "pull --ff-only failed (local changes?); leaving checkout as-is"
    ok "checkout at $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
  else
    section "Cloning JARVIS"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    ok "cloned to $INSTALL_DIR"
  fi
}

# ── Channel: CLI ─────────────────────────────────────────────────────────
install_cli() {
  if [ "${JARVIS_SKIP_CLI:-0}" = "1" ]; then warn "skipping CLI (JARVIS_SKIP_CLI=1)"; return; fi
  section "Installing CLI"
  (cd "$INSTALL_DIR/src/cli" && bun install --silent)
  mkdir -p "$LOCAL_BIN"
  ln -sf "$INSTALL_DIR/bin/jarvis"         "$LOCAL_BIN/jarvis"
  ln -sf "$INSTALL_DIR/bin/jarvis-desktop" "$LOCAL_BIN/jarvis-desktop"
  ok "deps installed"
  ok "symlinked $LOCAL_BIN/jarvis → $INSTALL_DIR/bin/jarvis"
  ok "symlinked $LOCAL_BIN/jarvis-desktop → $INSTALL_DIR/bin/jarvis-desktop"

  case ":$PATH:" in
    *":$LOCAL_BIN:"*) : ;;
    *) warn "$LOCAL_BIN is not in PATH — add it to your shell rc to use 'jarvis' globally" ;;
  esac
}

# ── Channel: Web (Next.js) ───────────────────────────────────────────────
install_web() {
  if [ "${JARVIS_SKIP_WEB:-0}" = "1" ]; then warn "skipping Web (JARVIS_SKIP_WEB=1)"; return; fi
  section "Installing Web (Next.js)"
  (cd "$INSTALL_DIR/src/web" && bun install --silent)
  ok "deps installed — run 'cd $INSTALL_DIR/src/web && bun dev' to start dev server"
}

# ── Channel: Voice agent ─────────────────────────────────────────────────
install_voice_agent() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then warn "skipping Voice Agent (JARVIS_SKIP_VOICE=1)"; return; fi
  section "Installing Voice Agent (~2–3 min; livekit-agents is heavy)"

  local va="$INSTALL_DIR/src/voice-agent"
  if [ ! -d "$va/.venv" ]; then
    python3 -m venv "$va/.venv"
    ok "created venv at $va/.venv"
  else
    ok "venv exists; reusing"
  fi
  "$va/.venv/bin/pip" install --quiet --upgrade pip
  "$va/.venv/bin/pip" install --quiet -r "$va/requirements.txt"
  ok "deps installed"

  install_systemd_units
}

install_systemd_units() {
  if ! have systemctl; then warn "no systemctl; skipping systemd unit install"; return; fi
  mkdir -p "$USER_SYSTEMD"

  # Substitute hardcoded paths in unit files so they work for any
  # install location. Voice-agent unit uses %h/Documents/Projects/jarvis
  # for most paths but hardcodes that subpath. Hub unit hardcodes the
  # full /home/ulrich/... path. Both need rewriting.
  local unit_voice="$USER_SYSTEMD/jarvis-voice-agent.service"
  local unit_hub="$USER_SYSTEMD/jarvis-hub.service"

  sed \
    -e "s|%h/Documents/Projects/jarvis|$INSTALL_DIR|g" \
    -e "s|/home/[^/]*/Documents/Projects/jarvis|$INSTALL_DIR|g" \
    -e "s|/home/[^/]*/jarvis|$INSTALL_DIR|g" \
    "$INSTALL_DIR/setup/systemd/jarvis-voice-agent.service" > "$unit_voice"
  sed \
    -e "s|/home/[^/]*/Documents/Projects/jarvis|$INSTALL_DIR|g" \
    -e "s|/home/[^/]*/jarvis|$INSTALL_DIR|g" \
    "$INSTALL_DIR/setup/systemd/jarvis-hub.service" > "$unit_hub"
  ok "installed unit: $unit_voice"
  ok "installed unit: $unit_hub"

  systemctl --user daemon-reload
  systemctl --user enable jarvis-voice-agent.service >/dev/null 2>&1 || warn "could not enable jarvis-voice-agent.service"
  ok "enabled jarvis-voice-agent.service (NOT started — configure .env first)"
}

# ── Channel: Desktop (Tauri) ─────────────────────────────────────────────
install_desktop() {
  if [ "${JARVIS_SKIP_DESKTOP:-0}" = "1" ]; then warn "skipping Desktop (JARVIS_SKIP_DESKTOP=1)"; return; fi
  section "Installing Desktop (Tauri) — first build takes 5–10 min"

  local dt="$INSTALL_DIR/src/desktop-tauri"
  (cd "$dt" && npm install --silent)
  ok "frontend deps installed"

  # CLAUDE.md rule: BOTH `npm run build` and `cargo build --release`
  # are required — npm run build alone does NOT ship JS changes
  # because Tauri embeds dist/ into the Rust binary at compile time.
  (cd "$dt" && npm run build --silent)
  ok "frontend built (dist/)"

  (cd "$dt/src-tauri" && cargo build --release)
  local bin="$dt/src-tauri/target/release/jarvis"
  if [ -x "$bin" ]; then
    ok "desktop binary at $bin"
  else
    # Tauri output name follows productName in tauri.conf.json — let
    # the user know to look around if our default guess missed.
    warn "expected $bin not found — check $dt/src-tauri/target/release/ for the binary name"
  fi
}

# ── .env template ────────────────────────────────────────────────────────
setup_env_template() {
  section "API key template"
  if [ -f "$INSTALL_DIR/.env" ]; then
    ok ".env already exists; not overwriting"
    return
  fi
  cat > "$INSTALL_DIR/.env" <<'EOF'
# JARVIS — centralized API keys.
# Each subproject's .env.local (or src/voice-agent/.env, etc.) holds
# subproject-specific vars and overrides these on collision.
# ~/.jarvis/keys.env overrides everything (Tray UI writes here).

# LLM providers (fill these in with real keys)
GROQ_API_KEY=
DEEPSEEK_API_KEY=
GOOGLE_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
KIMI_API_KEY=

# Optional knobs (uncomment + set if you use these)
# JARVIS_PROVIDER=deepseek
# OLLAMA_HOST=http://127.0.0.1:11434
# LANGCHAIN_TRACING_V2=true
# LANGCHAIN_API_KEY=
# LANGCHAIN_PROJECT=jarvis
EOF
  ok "created $INSTALL_DIR/.env (fill in your real keys before starting the voice agent)"
}

# ── Chrome extension instructions ────────────────────────────────────────
chrome_extension_step() {
  section "Chrome extension (manual final step)"
  cat <<EOF
  The JARVIS browser extension cannot be installed programmatically —
  Chrome blocks third-party extensions from being side-loaded by curl.

  To load it:
    1. Opening chrome://extensions in your default browser...
    2. Toggle 'Developer mode' (top-right)
    3. Click 'Load unpacked'
    4. Select: $INSTALL_DIR/src/extensions/jarvis-screen/

EOF
  if have xdg-open; then
    xdg-open "chrome://extensions/" >/dev/null 2>&1 &
    ok "chrome://extensions/ opened in your default browser"
  else
    warn "xdg-open not found — open chrome://extensions yourself"
  fi
}

# ── Final summary ────────────────────────────────────────────────────────
print_summary() {
  section "Done"
  cat <<EOF
  Install location:  $INSTALL_DIR
  CLI launcher:      $LOCAL_BIN/jarvis  (also $LOCAL_BIN/jarvis-desktop)

  Next steps:
    1. Edit $INSTALL_DIR/.env and fill in real API keys.
    2. Start the voice agent:
         systemctl --user start jarvis-voice-agent.service
       Logs:
         journalctl --user -u jarvis-voice-agent.service -f
    3. Try the CLI:
         jarvis
    4. Start the web app (optional):
         cd $INSTALL_DIR/src/web && bun dev
    5. Run the desktop app:
         $INSTALL_DIR/src/desktop-tauri/src-tauri/target/release/jarvis
       (or wherever cargo placed the binary)

  Re-run this script anytime to re-install or update a channel.
  Skip channels with JARVIS_SKIP_{CLI,VOICE,DESKTOP,WEB}=1.
EOF
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
  c_bold "JARVIS installer"
  detect_invocation
  check_prereqs
  # JARVIS_DRY_RUN=1 bails here — useful for verifying the script
  # detected the right install dir and your prereqs are sane before
  # committing to the 5–10 min cargo build.
  if [ "${JARVIS_DRY_RUN:-0}" = "1" ]; then
    section "Dry-run complete"
    sub "Detected/chosen install dir: $INSTALL_DIR"
    sub "All prereqs present. Re-run without JARVIS_DRY_RUN=1 to actually install."
    exit 0
  fi
  clone_or_update
  install_cli
  install_web
  install_voice_agent
  install_desktop
  setup_env_template
  chrome_extension_step
  print_summary
}

main "$@"
