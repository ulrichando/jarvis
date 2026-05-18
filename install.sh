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
  # src/hub/ is the TypeScript event-hub SDK used by src/cli/src/bridge/
  # (which the Tauri tray polls on 127.0.0.1:8765 for status). The
  # bridge imports from src/hub/client-core.ts, and bun resolves
  # 'ioredis' walking up from THAT file — so it has to be installed
  # in src/hub/node_modules, not src/cli/. (Live failure 2026-05-15:
  # without this, start-desktop.sh's bridge child crashed at boot with
  # "Cannot find package 'ioredis'", tray stayed red.)
  if [ -f "$INSTALL_DIR/src/hub/package.json" ]; then
    (cd "$INSTALL_DIR/src/hub" && bun install --silent)
    ok "hub TS deps installed (resolves ioredis for src/cli/src/bridge)"
  fi
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

  install_playwright_chromium "$va"
  install_systemd_units
}

# ── Channel: Playwright Chromium (~200MB, gated) ─────────────────────────
# Fetches the bundled Chromium binary Playwright needs for the browser
# subagent's CDP fallback path (tools/browser_cdp.py). Skip with
# JARVIS_SKIP_CDP=1 — the voice-agent still imports and runs without
# the binary; only the CDP fallback path bails with a clear error. The
# extension path is always available.
install_playwright_chromium() {
  local va="$1"
  if [ "${JARVIS_SKIP_CDP:-0}" = "1" ]; then
    warn "skipping Playwright Chromium (JARVIS_SKIP_CDP=1) — CDP fallback won't work"
    return
  fi
  # Already installed? Skip re-download.
  if [ -d "$HOME/.cache/ms-playwright" ] && ls "$HOME/.cache/ms-playwright" 2>/dev/null | grep -q "chromium"; then
    ok "Playwright Chromium already cached"
    return
  fi
  sub "About to download ~200MB of Chromium for browser CDP fallback"
  sub "(skip with JARVIS_SKIP_CDP=1; extension path always available)"
  # Non-interactive installs (e.g. curl|bash) → auto-yes.
  if [ ! -t 0 ]; then
    sub "non-interactive shell — proceeding with download"
  else
    read -r -p "  Download Playwright Chromium now? [Y/n] " reply
    if [ "${reply:-Y}" != "Y" ] && [ "${reply:-Y}" != "y" ] && [ -n "$reply" ]; then
      warn "skipped — run 'playwright install chromium' later if you want the fallback"
      return
    fi
  fi
  "$va/.venv/bin/playwright" install chromium
  ok "Playwright Chromium installed"
}

install_systemd_units() {
  if ! have systemctl; then warn "no systemctl; skipping systemd unit install"; return; fi
  mkdir -p "$USER_SYSTEMD"

  # Pre-create state + log dirs the units' ReadWritePaths= bind-mounts
  # require. Without these, the sandboxed units fail bring-up with
  # status=226/NAMESPACE (systemd refuses to bind-mount a non-existent
  # path even if the ExecStart script would create it). The units
  # have ExecStartPre fallbacks too — this is belt-and-suspenders.
  mkdir -p "$HOME/.local/share/jarvis/logs"   # voice-agent + hub + livekit-server log dest
  mkdir -p "$HOME/.jarvis/hub"                # hub state.db lives here
  mkdir -p "$HOME/.jarvis/snapshots"           # hourly backup snapshots
  chmod 700 "$HOME/.jarvis/snapshots"          # contains conversation + memory content

  local sed_path_subs=(
    -e "s|%h/Documents/Projects/jarvis|$INSTALL_DIR|g"
    -e "s|/home/[^/]*/Documents/Projects/jarvis|$INSTALL_DIR|g"
    -e "s|/home/[^/]*/jarvis|$INSTALL_DIR|g"
  )

  # Always-on services (voice-agent, voice-client, hub, livekit-server).
  for src in jarvis-voice-agent.service jarvis-voice-client.service jarvis-hub.service livekit-server.service; do
    sed "${sed_path_subs[@]}" "$INSTALL_DIR/setup/systemd/$src" > "$USER_SYSTEMD/$src"
    ok "installed unit: $USER_SYSTEMD/$src"
  done

  # Timer-driven maintenance units (added 2026-05-17). 3 services + 3
  # timers: hourly backup snapshot, daily log rotation, monthly
  # telemetry retention prune. The .service files are oneshots; the
  # .timer files are what get enabled.
  for src in \
      jarvis-backup-local.service jarvis-backup-local.timer \
      jarvis-log-rotate.service jarvis-log-rotate.timer \
      jarvis-retention-prune.service jarvis-retention-prune.timer; do
    if [ -f "$INSTALL_DIR/setup/systemd/$src" ]; then
      sed "${sed_path_subs[@]}" "$INSTALL_DIR/setup/systemd/$src" > "$USER_SYSTEMD/$src"
      ok "installed unit: $USER_SYSTEMD/$src"
    fi
  done

  systemctl --user daemon-reload

  # Enable always-on services (NOT started — user runs them after
  # configuring .env). Enable order matters: SFU + Redis first, then
  # agent + client.
  for unit in livekit-server.service jarvis-hub.service jarvis-voice-agent.service jarvis-voice-client.service; do
    systemctl --user enable "$unit" >/dev/null 2>&1 \
      && ok "enabled $unit (NOT started — configure .env first)" \
      || warn "could not enable $unit"
  done

  # Enable + start the maintenance timers — these are safe to start
  # immediately (they don't depend on .env or running provider APIs).
  # First fire happens per OnCalendar (hourly / 02:00 daily / 03:00
  # monthly-1st); Persistent=true catches up if laptop was off.
  for unit in jarvis-backup-local.timer jarvis-log-rotate.timer jarvis-retention-prune.timer; do
    if [ -f "$USER_SYSTEMD/$unit" ]; then
      systemctl --user enable --now "$unit" >/dev/null 2>&1 \
        && ok "enabled + started $unit" \
        || warn "could not enable $unit"
    fi
  done
}

# ── Bubblewrap (bash-tool sandbox runtime) ───────────────────────────────
install_bubblewrap() {
  if have bwrap; then
    ok "bubblewrap already installed ($(bwrap --version 2>/dev/null | head -1))"
    return
  fi
  # bubblewrap is the user-namespace sandbox the bash tool wraps every
  # subprocess in (tools/bash.py, added 2026-05-17 per enterprise plan
  # §P0-SEC-7). Without it, the bash tool falls back to unsandboxed
  # /bin/bash -c — works but loses the ~/.ssh/AWS/GPG tmpfs masks
  # and the network-namespace gate. apt + pacman covered; other distros
  # warn-only since the user-namespace approach is universal but
  # package names vary.
  if have apt-get && [ "$JARVIS_DRY_RUN" != "1" ]; then
    info "installing bubblewrap via apt..."
    if sudo -n apt-get install -y bubblewrap >/dev/null 2>&1; then
      ok "bubblewrap installed"
    else
      warn "couldn't apt-install bubblewrap (sudo? offline?); the bash tool will run un-sandboxed."
      warn "to enable: sudo apt install bubblewrap"
    fi
  elif have pacman && [ "$JARVIS_DRY_RUN" != "1" ]; then
    info "installing bubblewrap via pacman..."
    if sudo -n pacman -S --noconfirm bubblewrap >/dev/null 2>&1; then
      ok "bubblewrap installed"
    else
      warn "couldn't pacman-install bubblewrap; the bash tool will run un-sandboxed."
      warn "to enable: sudo pacman -S bubblewrap"
    fi
  else
    warn "bubblewrap NOT installed (no apt/pacman detected). The bash"
    warn "tool will run un-sandboxed. Install bubblewrap manually if"
    warn "you want ~/.ssh/AWS/GPG tmpfs masking + network namespace gate."
    warn "Documented at: src/voice-agent/tools/bash.py (§P0-SEC-7)"
  fi
}

# ── Bridge auth token (pre-generated for first-run UX) ────────────────────
generate_bridge_token() {
  local token_file="$HOME/.jarvis/local-api-token.env"
  if [ -f "$token_file" ]; then
    ok "bridge token already exists at $token_file"
    return
  fi
  mkdir -p "$HOME/.jarvis"
  umask 077
  # 32 bytes urandom → base64 → 43 url-safe chars (no padding).
  local token
  token="$(head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 43)"
  printf 'JARVIS_LOCAL_API_TOKEN=%s\n' "$token" > "$token_file"
  chmod 600 "$token_file"
  ok "generated bridge auth token at $token_file (chmod 600)"
  # Plumb the token into src/web/.env.local too so the Next.js
  # middleware's bearer check (src/web/src/middleware.ts) has the
  # value at `next start` time without depending on start-desktop.sh
  # having already run.
  local web_env="$INSTALL_DIR/src/web/.env.local"
  if [ -f "$web_env" ] && ! grep -q "^JARVIS_LOCAL_API_TOKEN=" "$web_env"; then
    printf '\n# Bearer token for /api/* middleware (matches the bridge token).\nJARVIS_LOCAL_API_TOKEN=%s\nJARVIS_REQUIRE_LOCAL_AUTH=1\n' "$token" >> "$web_env"
    chmod 600 "$web_env"
    ok "appended JARVIS_LOCAL_API_TOKEN + REQUIRE_LOCAL_AUTH=1 to $web_env"
  fi
}

# ── External services: LiveKit keys + Redis ──────────────────────────────
setup_livekit_keys() {
  local keys="$HOME/.jarvis/livekit-keys.yaml"
  local va_env="$INSTALL_DIR/src/voice-agent/.env"

  # If keys file already exists in proper YAML format (key: secret on
  # one line, no whitespace before the colon), leave it alone. Format
  # check: first non-comment line must match `^[A-Za-z0-9]+:[[:space:]]`.
  if [ -s "$keys" ] && awk '/^[^#]/ && /^[A-Za-z0-9]+:[[:space:]]/ {found=1; exit} END {exit !found}' "$keys"; then
    ok "LiveKit keys already at $keys"
    return
  fi
  mkdir -p "$HOME/.jarvis"

  # Prefer the LIVEKIT_API_KEY / LIVEKIT_API_SECRET already in
  # voice-agent/.env — that's the source of truth the running agent
  # reads via systemd EnvironmentFile=. Writing the YAML from those
  # keeps server + client in sync. (livekit-server.bin's
  # generate-keys subcommand emits 'API Key: …\nAPI Secret: …' in
  # human-readable form, NOT the YAML the key_file directive expects;
  # using that output directly produced a 401 handshake failure on
  # first run, 2026-05-15.)
  local lk_key="" lk_secret=""
  if [ -f "$va_env" ]; then
    lk_key=$(grep "^LIVEKIT_API_KEY=" "$va_env" | head -1 | sed 's/^LIVEKIT_API_KEY=//' | tr -d '"'"'"' ')
    lk_secret=$(grep "^LIVEKIT_API_SECRET=" "$va_env" | head -1 | sed 's/^LIVEKIT_API_SECRET=//' | tr -d '"'"'"' ')
  fi

  if [ -n "$lk_key" ] && [ -n "$lk_secret" ]; then
    # Backup any existing malformed file before overwriting.
    [ -s "$keys" ] && mv "$keys" "$keys.bak-$(date +%s)"
    printf '%s: %s\n' "$lk_key" "$lk_secret" > "$keys"
    chmod 600 "$keys"
    ok "wrote $keys from voice-agent/.env LIVEKIT_API_KEY/SECRET (chmod 600)"
    return
  fi

  # No .env keys yet — generate a fresh pair and write both files in sync.
  local gen_out
  if ! gen_out=$("$INSTALL_DIR/src/voice-agent/livekit-server.bin" generate-keys 2>/dev/null); then
    warn "couldn't generate LiveKit keys; create $keys manually:"
    sub "$INSTALL_DIR/src/voice-agent/livekit-server.bin generate-keys"
    sub "(extract the API Key + API Secret lines, write '<key>: <secret>' to $keys, chmod 600)"
    return
  fi
  local new_key new_secret
  new_key=$(printf '%s\n' "$gen_out" | awk -F': +' '/^API Key:/ {print $2; exit}')
  new_secret=$(printf '%s\n' "$gen_out" | awk -F': +' '/^API Secret:/ {print $2; exit}')
  if [ -z "$new_key" ] || [ -z "$new_secret" ]; then
    warn "couldn't parse generated keys; check $INSTALL_DIR/src/voice-agent/livekit-server.bin generate-keys output"
    return
  fi
  printf '%s: %s\n' "$new_key" "$new_secret" > "$keys"
  chmod 600 "$keys"
  ok "generated fresh LiveKit keys → $keys"

  # And append them to voice-agent/.env so the agent uses the same pair.
  if [ -f "$va_env" ]; then
    {
      echo ""
      echo "# LiveKit auth — generated by install.sh on $(date +%Y-%m-%d)"
      echo "LIVEKIT_API_KEY=$new_key"
      echo "LIVEKIT_API_SECRET=$new_secret"
    } >> "$va_env"
    ok "appended LIVEKIT_API_KEY/SECRET to $va_env"
  fi
}

# ── PipeWire / WirePlumber: auto-profile so mic + speakers coexist ───────
# Some hardware (notably Dell Latitudes with Realtek ALC256/ALC3246)
# ships WirePlumber with `api.acp.auto-profile=false`, which hides the
# combined analog-stereo+input profile and forces apps onto `pro-audio`.
# pro-audio then grabs the raw `hw:` device exclusively, blocking other
# apps from sharing the mic. Installing this config flips both knobs
# on; it's a no-op on hardware that already exposes the duplex profile.
# Live failure 2026-05-15 — without it the voice-client locked the mic
# against every other app on the box.
install_audio_profile() {
  if [ "${JARVIS_SKIP_AUDIO_PROFILE:-0}" = "1" ]; then
    warn "skipping audio-profile config (JARVIS_SKIP_AUDIO_PROFILE=1)"; return
  fi
  if ! have wpctl; then
    warn "no wpctl — PipeWire/WirePlumber not installed, skipping audio-profile config"
    return
  fi
  local src="$INSTALL_DIR/setup/audio/99-jarvis-auto-profile.conf"
  local dst="/etc/wireplumber/wireplumber.conf.d/99-jarvis-auto-profile.conf"
  if [ ! -f "$src" ]; then
    warn "audio-profile template missing at $src — skipping"; return
  fi
  # `sudo -n` so a non-interactive curl-pipe install doesn't hang. If
  # sudo isn't NOPASSWD, fall through to instructions.
  if sudo -n mkdir -p "$(dirname "$dst")" 2>/dev/null \
     && sudo -n cp "$src" "$dst" 2>/dev/null; then
    ok "installed $dst"
    # Reload WirePlumber (user-level) so the new rule is picked up. The
    # next time the user has a wedge-y profile they'll also need to clear
    # ~/.local/state/wireplumber/default-profile, but on a fresh install
    # there's no saved override yet so a plain restart is enough.
    if systemctl --user restart wireplumber.service >/dev/null 2>&1; then
      ok "reloaded wireplumber.service"
    fi
  else
    warn "could not auto-install audio-profile config (sudo not NOPASSWD); run manually:"
    sub "sudo mkdir -p $(dirname "$dst") && sudo cp $src $dst"
    sub "systemctl --user restart wireplumber.service"
  fi
}

setup_redis() {
  # jarvis-hub talks to redis at 127.0.0.1:6379. We use the system
  # redis-server.service (not a user unit) because it's the conventional
  # path and your distro almost certainly installs it that way.
  if ! have redis-server; then
    warn "redis-server not installed — needed by jarvis-hub.service"
    sub "Install: sudo apt install redis-server   (Debian/Ubuntu/Kali)"
    sub "         sudo pacman -S redis            (Arch)"
    return
  fi
  if systemctl is-active --quiet redis-server.service 2>/dev/null; then
    ok "redis-server.service is already active"
    return
  fi
  # Try to enable + start; if sudo prompts for password and we're in
  # a non-interactive curl-pipe, this will fail. Falls through to
  # printed instructions.
  if sudo -n systemctl enable --now redis-server.service >/dev/null 2>&1; then
    ok "enabled + started redis-server.service"
  else
    warn "could not auto-start redis-server (sudo not NOPASSWD); run manually:"
    sub "sudo systemctl enable --now redis-server.service"
  fi
}

# ── Computer-use subagent dependencies (optional) ────────────────────────
check_computer_use_deps() {
  # Computer-use subagent dependencies (optional — only needed if
  # JARVIS_SUBAGENT_COMPUTER_USE=1 is set). Probe and hint; don't fail
  # the install if absent.
  echo
  sub "Checking computer_use subagent deps (optional)..."
  if ! "$INSTALL_DIR/src/voice-agent/.venv/bin/python" -c "import mss" 2>/dev/null; then
    warn "mss not installed in voice-agent venv. To enable computer_use:"
    sub "$INSTALL_DIR/src/voice-agent/.venv/bin/pip install mss"
  fi
  if ! dpkg -l python3-pyatspi >/dev/null 2>&1; then
    warn "python3-pyatspi not installed. To enable a11y grounding:"
    sub "sudo apt install -y python3-pyatspi gir1.2-atspi-2.0"
  fi
  if ! which xdpyinfo >/dev/null 2>&1; then
    warn "xdpyinfo not found. For X11 session probing:"
    sub "sudo apt install -y x11-utils"
  fi
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
  # Cargo names the binary after [package].name in Cargo.toml — currently
  # 'jarvis-desktop'. NOT 'jarvis' (that's the productName in
  # tauri.conf.json, which only affects window title + the bundled
  # .app/.deb metadata, not the bare ELF).
  local bin="$dt/src-tauri/target/release/jarvis-desktop"
  if [ -x "$bin" ]; then
    ok "desktop binary at $bin ($(du -h "$bin" | cut -f1))"
  else
    warn "expected $bin not found — check $dt/src-tauri/target/release/ for the binary name"
  fi

  install_desktop_entry
}

# ── App menu .desktop file ───────────────────────────────────────────────
install_desktop_entry() {
  local apps_dir="$HOME/.local/share/applications"
  local entry="$apps_dir/jarvis.desktop"
  local exec_path="$INSTALL_DIR/src/desktop-tauri/src-tauri/target/release/jarvis-desktop"
  # The Tauri default icons (src-tauri/icons/{32x32,128x128,tray}.png)
  # are placeholder Tauri logos from `tauri init` (cyan circle, ~500 B).
  # The actual JARVIS branding is the concentric-rings logo committed
  # with the Chrome extension. Reuse it so the app-menu entry matches
  # what JARVIS looks like everywhere else.
  local icon_path="$INSTALL_DIR/src/extensions/jarvis-screen/icon128.png"

  mkdir -p "$apps_dir"
  cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=JARVIS
GenericName=Voice Assistant
Comment=Voice-first AI assistant (Tauri desktop UI)
Exec=$exec_path
Icon=$icon_path
Terminal=false
Categories=Utility;Office;AudioVideo;
StartupNotify=true
StartupWMClass=JARVIS
Keywords=AI;assistant;voice;LiveKit;
EOF
  chmod 644 "$entry"
  ok "installed app-menu entry: $entry"

  if [ ! -x "$exec_path" ]; then
    warn "Tauri binary not yet built — launcher will fail until cargo build --release completes"
    sub "Build now: (cd $INSTALL_DIR/src/desktop-tauri/src-tauri && cargo build --release)"
  fi

  # Refresh GNOME/KDE/XFCE app cache so the entry appears without logout.
  if have update-desktop-database; then
    update-desktop-database "$apps_dir" >/dev/null 2>&1 \
      && ok "refreshed app menu cache" \
      || warn "could not refresh app menu cache (entry may take a moment to appear)"
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

# Sandbox / safety knobs (uncomment to override defaults)
# JARVIS_BASH_BWRAP=0           # disable bash-tool bubblewrap sandbox
#                               # (test / debug only — reduces blast-radius
#                               # protection on ~/.ssh, ~/.aws, ~/.gnupg)
# JARVIS_REQUIRE_LOCAL_AUTH=1   # require Bearer token on bridge + web /api/*
#                               # (default on when start-desktop.sh launches;
#                               # set explicitly if you bypass that script)
# JARVIS_DAILY_COST_CEILING_USD=5  # canary alert threshold (P1-OBS-1)
EOF
  # Lock the file to 0600 so other local users / containers / web pages
  # can't read the API keys. (Per security review 2026-05-16: previously
  # 0664; 22 prod API keys exposed.)
  chmod 600 "$INSTALL_DIR/.env"
  ok "created $INSTALL_DIR/.env (chmod 600 — fill in your real keys before starting the voice agent)"
}

# ── Chrome extension instructions ────────────────────────────────────────
chrome_extension_step() {
  section "Chrome extension (manual final step)"
  cat <<EOF
  The JARVIS browser extension cannot be installed programmatically —
  Chrome blocks third-party extensions from being side-loaded by curl.

  To load it:
    1. Open chrome://extensions in Chrome/Chromium
    2. Toggle 'Developer mode' (top-right)
    3. Click 'Load unpacked'
    4. Select: $INSTALL_DIR/src/extensions/jarvis-screen/

EOF
  # chrome:// URLs only work in a Chromium-based browser. xdg-open will
  # route to the default browser, which is usually Firefox — and
  # Firefox can't render chrome:// URLs. Try each known Chromium-family
  # browser by name; if none is found, just print the path so the
  # user can copy/paste.
  local chrome_bin=""
  for cand in google-chrome google-chrome-stable chromium chromium-browser brave-browser microsoft-edge; do
    if have "$cand"; then chrome_bin="$cand"; break; fi
  done
  if [ -n "$chrome_bin" ]; then
    "$chrome_bin" "chrome://extensions/" >/dev/null 2>&1 &
    disown 2>/dev/null || true
    ok "opened chrome://extensions/ in $chrome_bin"
  else
    warn "no Chromium-based browser found on PATH — open the URL manually"
    sub "Path to copy: $INSTALL_DIR/src/extensions/jarvis-screen/"
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
    2. Start the SFU + hub + voice agent + voice client (in this order —
       voice-agent requires livekit-server, jarvis-hub requires Redis,
       voice-client is the desktop's native PortAudio bridge):
         sudo systemctl enable --now redis-server.service   # if not done
         systemctl --user start livekit-server.service
         systemctl --user start jarvis-hub.service
         systemctl --user start jarvis-voice-agent.service
         systemctl --user start jarvis-voice-client.service
       Logs:
         journalctl --user -u jarvis-voice-agent.service -f
         journalctl --user -u jarvis-voice-client.service -f
    3. Try the CLI:
         jarvis
    4. Start the web app (optional):
         cd $INSTALL_DIR/src/web && bun dev
    5. Run the desktop app (Tauri):
         $INSTALL_DIR/src/desktop-tauri/src-tauri/target/release/jarvis-desktop
       (or click 'JARVIS' in your app launcher — Ctrl+Shift+Space toggles
       click-through once it's running)

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
  install_bubblewrap     # bash-tool sandbox runtime (§P0-SEC-7)
  generate_bridge_token  # ~/.jarvis/local-api-token.env + web .env.local
  setup_livekit_keys
  setup_redis
  check_computer_use_deps  # optional probes for computer_use subagent
  install_audio_profile
  setup_env_template
  chrome_extension_step
  print_summary
}

main "$@"
