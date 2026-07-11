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
# Skip big voice-model downloads (STT model ~1.5GB, vosk ~40MB): JARVIS_SKIP_MODELS=1
# Skip the local Kokoro TTS container: JARVIS_SKIP_KOKORO=1
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
  # The CLI bridge (src/cli/src/bridge/) has an orphaned import that
  # won't resolve at runtime — accepted while src/cli/ remains off-limits.
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
  # JARVIS login (better-auth): signing secret + DB migrations so the login
  # gate, accounts, and chat persistence work on a fresh install.
  local web_env="$INSTALL_DIR/src/web/.env.local"
  if [ -f "$web_env" ] && ! grep -q "^BETTER_AUTH_SECRET=" "$web_env"; then
    local auth_secret
    auth_secret=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
    printf '\n# better-auth (JARVIS login) — signs sessions; keep secret.\nBETTER_AUTH_SECRET=%s\nBETTER_AUTH_URL=http://localhost:3000\n' "$auth_secret" >> "$web_env"
    chmod 600 "$web_env"
    ok "generated BETTER_AUTH_SECRET"
  fi
  if [ -f "$web_env" ] && grep -q "^DATABASE_URL=" "$web_env"; then
    if (cd "$INSTALL_DIR/src/web" && bun run db:migrate >/dev/null 2>&1); then
      ok "applied web DB migrations (auth + chat tables)"
    else
      warn "web DB migration skipped — run 'cd src/web && bun run db:migrate' once Postgres is reachable"
    fi
  else
    warn "DATABASE_URL not in .env.local — login + chat need it; then run 'cd src/web && bun run db:migrate'"
  fi
  ok "deps installed — run 'cd $INSTALL_DIR/src/web && bun dev' to start dev server"
}

# ── Channel: Voice agent ─────────────────────────────────────────────────
# Uses uv (Astral) for Python install + venv + dependency sync. uv is
# 10-100x faster than pip for cold resolves, handles the Python install
# itself if absent, and matches the Windows installer (install.ps1) so
# both platforms run the same package manager. Falls back to system
# python3 + pip when uv is unavailable or the user opts out via
# JARVIS_NO_UV=1.
install_voice_agent() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then warn "skipping Voice Agent (JARVIS_SKIP_VOICE=1)"; return; fi
  section "Installing Voice Agent (~2–3 min; livekit-agents is heavy)"

  local va="$INSTALL_DIR/src/voice-agent"
  local use_uv=0
  if [ "${JARVIS_NO_UV:-0}" != "1" ]; then
    if have uv; then
      use_uv=1
      sub "using uv ($(uv --version 2>/dev/null | head -1))"
    else
      sub "uv not installed; installing via astral.sh/uv installer (no sudo needed)"
      # Astral's official installer drops uv into ~/.local/bin (or
      # ~/.cargo/bin on systems where that's the convention). Idempotent;
      # safe to re-run.
      if curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; then
        # Refresh PATH for this run so we see the newly-installed binary.
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        if have uv; then
          use_uv=1
          ok "installed uv ($(uv --version 2>/dev/null | head -1))"
        fi
      fi
      if [ "$use_uv" != "1" ]; then
        warn "uv install failed -- falling back to python3 -m venv + pip (slower)"
      fi
    fi
  fi

  if [ "$use_uv" = "1" ]; then
    if [ ! -d "$va/.venv" ]; then
      # `uv venv` creates the venv and pins the Python version in one
      # step; no ensurepip / pip-upgrade dance needed afterwards.
      uv venv "$va/.venv" --python 3.13 || uv venv "$va/.venv" --python 3.12 || uv venv "$va/.venv"
      ok "created venv at $va/.venv via uv"
    else
      ok "venv exists; reusing"
    fi
    # Tell uv where to install (no activation needed).
    VIRTUAL_ENV="$va/.venv" UV_PROJECT_ENVIRONMENT="$va/.venv" \
      uv pip install --requirement "$va/requirements.txt"
    ok "deps installed via uv"
  else
    if [ ! -d "$va/.venv" ]; then
      python3 -m venv "$va/.venv"
      ok "created venv at $va/.venv"
    else
      ok "venv exists; reusing"
    fi
    "$va/.venv/bin/pip" install --quiet --upgrade pip
    "$va/.venv/bin/pip" install --quiet -r "$va/requirements.txt"
    ok "deps installed via pip"
  fi

  install_playwright_chromium "$va"
  install_systemd_units

  # Harden secret-bearing env files (owner-only)
  for f in "$va/.env" "$HOME/.jarvis/keys.env" "$HOME/.jarvis/local-api-token.env"; do
    [ -f "$f" ] && chmod 600 "$f"
  done
  ok "hardened env file permissions (chmod 600)"
}

# ── Channel: Playwright Chromium (~200MB, gated) ─────────────────────────
# Fetches the bundled Chromium binary Playwright needs for the browser
# subagent's CDP fallback path (tools/browser_cdp.py). Skip with
# JARVIS_SKIP_CDP=1 — the voice-agent still imports and runs without
# the binary; only the CDP fallback path bails with a clear error.
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
  sub "(skip with JARVIS_SKIP_CDP=1 — CDP fallback won't work without it)"
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
  mkdir -p "$HOME/.local/share/jarvis/logs"   # voice-agent + livekit-server log dest
  mkdir -p "$HOME/.jarvis/snapshots"           # hourly backup snapshots
  mkdir -p "$HOME/.jarvis/dep-check"           # dependency health check results
  chmod 700 "$HOME/.jarvis/snapshots"          # contains telemetry detail

  local sed_path_subs=(
    -e "s|%h/Documents/Projects/jarvis|$INSTALL_DIR|g"
    -e "s|/home/[^/]*/Documents/Projects/jarvis|$INSTALL_DIR|g"
    -e "s|/home/[^/]*/jarvis|$INSTALL_DIR|g"
  )

  # Always-on services (voice-agent, voice-client, livekit-server).
  for src in jarvis-voice-agent.service jarvis-voice-client.service livekit-server.service; do
    sed "${sed_path_subs[@]}" "$INSTALL_DIR/setup/systemd/$src" > "$USER_SYSTEMD/$src"
    ok "installed unit: $USER_SYSTEMD/$src"
  done

  # Timer-driven maintenance units (added 2026-05-17). 3 services + 3
  # timers: hourly backup snapshot, daily log rotation, monthly
  # telemetry retention prune. The .service files are oneshots; the
  # .timer files are what get enabled.
  for src in \
      jarvis-backup-local.service jarvis-backup-local.timer \
      jarvis-backup-offsite.service jarvis-backup-offsite.timer \
      jarvis-backup-verify.service jarvis-backup-verify.timer \
      jarvis-alert@.service \
      jarvis-notification-listener.service \
      jarvis-slo.service jarvis-slo.timer \
      jarvis-key-age.service jarvis-key-age.timer \
      jarvis-log-rotate.service jarvis-log-rotate.timer \
      jarvis-retention-prune.service jarvis-retention-prune.timer \
      jarvis-dep-check.service jarvis-dep-check.timer \
      jarvis-dep-update.service; do
    if [ -f "$INSTALL_DIR/setup/systemd/$src" ]; then
      sed "${sed_path_subs[@]}" "$INSTALL_DIR/setup/systemd/$src" > "$USER_SYSTEMD/$src"
      ok "installed unit: $USER_SYSTEMD/$src"
    fi
  done

  systemctl --user daemon-reload

  # Enable always-on services (NOT started — user runs them after
  # configuring .env). Enable order matters: SFU first, then agent +
  # client.
  for unit in livekit-server.service jarvis-voice-agent.service jarvis-voice-client.service; do
    systemctl --user enable "$unit" >/dev/null 2>&1 \
      && ok "enabled $unit (NOT started — configure .env first)" \
      || warn "could not enable $unit"
  done

  # Enable + start the maintenance timers — these are safe to start
  # immediately (they don't depend on .env or running provider APIs).
  # First fire happens per OnCalendar (hourly / 02:00 daily / 03:00
  # monthly-1st); Persistent=true catches up if laptop was off.
  for unit in jarvis-notification-listener.service jarvis-backup-local.timer jarvis-backup-offsite.timer jarvis-backup-verify.timer jarvis-slo.timer jarvis-key-age.timer jarvis-log-rotate.timer jarvis-retention-prune.timer jarvis-dep-check.timer; do
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
  if have apt-get && [ "${JARVIS_DRY_RUN:-0}" != "1" ]; then
    sub "installing bubblewrap via apt..."
    if sudo -n apt-get install -y bubblewrap >/dev/null 2>&1; then
      ok "bubblewrap installed"
    else
      warn "couldn't apt-install bubblewrap (sudo? offline?); the bash tool will run un-sandboxed."
      warn "to enable: sudo apt install bubblewrap"
    fi
  elif have pacman && [ "${JARVIS_DRY_RUN:-0}" != "1" ]; then
    sub "installing bubblewrap via pacman..."
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

# ── Fetch livekit-server binary (not tracked in git) ─────────────────────
# Downloads the pinned release tarball, extracts livekit-server, and
# verifies SHA-256 against setup/livekit-server.bin.sha256.
# Skipped when the binary already exists (install is idempotent).
ensure_livekit_binary() {
  local bin="$INSTALL_DIR/src/voice-agent/livekit-server.bin"
  if [ -x "$bin" ]; then
    ok "livekit-server.bin already present; skipping download"
    return
  fi

  local version="1.11.0"
  # Arch-aware release asset — the old hardcoded amd64 URL silently shipped
  # an unrunnable binary to arm64 boxes.
  local arch lk_arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64)  lk_arch="amd64" ;;
    aarch64|arm64) lk_arch="arm64" ;;
    *)
      warn "unsupported arch '$arch' for livekit-server auto-download — voice needs the SFU."
      sub "Fetch a build manually from https://github.com/livekit/livekit/releases and place it at $bin"
      return 0 ;;
  esac
  local url="https://github.com/livekit/livekit/releases/download/v${version}/livekit_${version}_linux_${lk_arch}.tar.gz"
  local sha_file="$INSTALL_DIR/setup/livekit-server.bin.sha256"

  section "Fetching livekit-server binary v${version} (~50 MB)"
  sub "URL: $url"

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT RETURN

  # Download / extract failures are WARN, not fatal — a network flake must
  # not abort the rest of the install. verify_voice_stack() reports the
  # missing binary at the end; re-run install.sh to retry.
  if ! curl -fL --progress-bar -o "$tmp_dir/livekit.tar.gz" "$url"; then
    warn "failed to download livekit-server tarball from $url"
    sub "voice needs the SFU — re-run ./install.sh once the network is back"
    return 0
  fi

  if ! tar -xzf "$tmp_dir/livekit.tar.gz" -C "$tmp_dir"; then
    warn "failed to extract livekit-server tarball — re-run ./install.sh to retry"
    return 0
  fi

  local extracted
  extracted=$(find "$tmp_dir" -maxdepth 2 -name 'livekit-server' -type f | head -1)
  if [ -z "$extracted" ]; then
    warn "livekit-server binary not found in tarball — expected 'livekit-server' entry"
    return 0
  fi

  # Verify checksum against pinned hash. The pin is for the linux_amd64
  # build only — other arches download unpinned (warned).
  if [ "$lk_arch" != "amd64" ]; then
    warn "no pinned SHA-256 for linux_${lk_arch} — skipping checksum verification"
  elif [ -f "$sha_file" ]; then
    # sha256sum -c expects lines like "<hash>  <filename>".
    # Rewrite the entry so the path points at the extracted file.
    local pinned_hash
    pinned_hash=$(awk '{print $1}' "$sha_file")
    local actual_hash
    actual_hash=$(sha256sum "$extracted" | awk '{print $1}')
    if [ "$actual_hash" != "$pinned_hash" ]; then
      die "livekit-server.bin SHA-256 MISMATCH — expected $pinned_hash, got $actual_hash. Aborting install."
    fi
    ok "SHA-256 verified: $pinned_hash"
  else
    warn "setup/livekit-server.bin.sha256 not found — skipping checksum verification"
  fi

  cp "$extracted" "$bin"
  chmod +x "$bin"
  ok "livekit-server.bin installed at $bin"
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

# ── Local voice stack: GPU detect + STT + TTS + models ───────────────────
# The voice agent is 100% on-device for speech (CLAUDE.md): STT is
# faster-whisper (large-v3-turbo, GPU) with JARVIS_STT_LOCAL_ONLY=1, TTS
# is Kokoro (local HTTP service, :8880) with Edge-TTS as cloud fallback.
# None of that was previously installed here — a fresh box came up with
# NO STT rung at all (build_stt_chain raises "no STT available") and the
# voice-agent unit failed outright because its non-optional
# EnvironmentFile= (src/voice-agent/.env) didn't exist. These functions
# detect + install each piece; every failure is a WARN, never an abort.

# True iff an NVIDIA GPU is visible via the driver. GPU is load-bearing
# for STT: CPU transcription of large-v3-turbo is ~25 s per 3 s clip →
# utterances drop → JARVIS goes intermittently silent.
detect_nvidia_gpu() {
  have nvidia-smi && nvidia-smi -L 2>/dev/null | grep -q '^GPU'
}

# _env_default <file> <VAR> <value> — set VAR only when unset/empty in
# <file> (never clobbers an existing value; append-or-fill via _env_upsert).
_env_default() {
  local file="$1" var="$2" value="$3"
  [ -n "$(_env_get "$file" "$var")" ] && return 0
  _env_upsert "$file" "$var" "$value"
}

# True iff the faster-whisper model <$1> is already in the HuggingFace
# cache (models--<org>--faster-whisper-<name> snapshot dir).
_stt_model_cached() {
  local model="$1" hub="${HF_HOME:-$HOME/.cache/huggingface}/hub"
  compgen -G "$hub/models--*--faster-whisper-${model}" >/dev/null 2>&1
}

# pip-install into the voice venv, preferring uv when available (mirrors
# install_voice_agent's package-manager choice).
_venv_pip_install() {
  local va="$INSTALL_DIR/src/voice-agent"
  if have uv && [ "${JARVIS_NO_UV:-0}" != "1" ]; then
    VIRTUAL_ENV="$va/.venv" UV_PROJECT_ENVIRONMENT="$va/.venv" uv pip install "$@"
  else
    "$va/.venv/bin/pip" install --quiet "$@"
  fi
}

# Bootstrap src/voice-agent/.env — the systemd unit's FIRST EnvironmentFile=
# is NON-optional, so a missing .env fails the unit at start. Seed from the
# committed .env.example, then fill in the local-voice flags the example
# doesn't carry (JARVIS_LOCAL_STT_* / JARVIS_LOCAL_TTS_*). Never overwrites
# an existing file or an already-set var.
setup_voice_env() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  section "Voice-agent .env (local STT + TTS config)"
  local va="$INSTALL_DIR/src/voice-agent"
  local va_env="$va/.env"
  local fresh=0

  if [ -f "$va_env" ]; then
    ok ".env already exists at $va_env; keeping it"
  elif [ -f "$va/.env.example" ]; then
    # Localize the template's hardcoded /home/ulrich paths (XAUTHORITY).
    sed "s|/home/ulrich|$HOME|g" "$va/.env.example" > "$va_env"
    chmod 600 "$va_env"
    fresh=1
    ok "created $va_env from .env.example"
  else
    : > "$va_env"; chmod 600 "$va_env"
    fresh=1
    warn ".env.example missing — created empty $va_env"
    _env_default "$va_env" LIVEKIT_URL "ws://127.0.0.1:7880"
  fi

  # Local STT (faster-whisper). Enabled everywhere; device follows the GPU.
  local gpu=0
  detect_nvidia_gpu && gpu=1
  _env_default "$va_env" JARVIS_LOCAL_STT_ENABLED 1
  _env_default "$va_env" JARVIS_LOCAL_STT_MODEL   large-v3-turbo
  _env_default "$va_env" JARVIS_LOCAL_STT_PRIMARY 1
  if [ "$gpu" = "1" ]; then
    _env_default "$va_env" JARVIS_LOCAL_STT_DEVICE  cuda
    _env_default "$va_env" JARVIS_LOCAL_STT_COMPUTE int8_float16
    # Fresh install on a GPU box → the live architecture: 100% on-device STT.
    # Only defaulted on a FRESH .env so we never strip a cloud rung someone
    # deliberately configured on an existing box.
    [ "$fresh" = "1" ] && _env_default "$va_env" JARVIS_STT_LOCAL_ONLY 1
    ok "local STT configured for GPU (cuda / int8_float16)"
  else
    _env_default "$va_env" JARVIS_LOCAL_STT_DEVICE  cpu
    _env_default "$va_env" JARVIS_LOCAL_STT_COMPUTE int8
    warn "NO NVIDIA GPU DETECTED — STT will run on CPU."
    warn "CPU transcription of large-v3-turbo is ~25 s per clip; most utterances"
    warn "will be DROPPED and the assistant will seem silent/deaf. Strongly consider:"
    sub "- an NVIDIA GPU + driver (nvidia-smi must work), then re-run ./install.sh, or"
    sub "- a DEEPGRAM_API_KEY in $va_env (cloud STT primary; local stays as fallback)"
  fi

  # Local TTS (Kokoro, HTTP service on :8880) — Edge-TTS remains the
  # auth-free cloud fallback, so voice output survives without Kokoro.
  _env_default "$va_env" JARVIS_LOCAL_TTS_ENABLED 1
  _env_default "$va_env" JARVIS_LOCAL_TTS_ENGINE  kokoro
  _env_default "$va_env" JARVIS_LOCAL_TTS_URL     "http://127.0.0.1:8880/v1"
  _env_default "$va_env" JARVIS_LOCAL_TTS_VOICE   af_bella
  _env_default "$va_env" JARVIS_LOCAL_TTS_PRIMARY 1
  ok "local TTS (kokoro) configured — Edge-TTS stays as fallback"

  # LLM offline failover (Stage 1, 2026-07-10): append the local model as the
  # LAST FallbackAdapter rung so voice survives with no internet — reached ONLY
  # after every cloud rung fails (providers/llm.py::wrap_local_failover). Seed
  # ON by default so a fresh box has offline parity; _env_default never
  # overrides a value the user already set. Distinct from JARVIS_LOCAL_LLM_ENABLED
  # (that one makes local the rung-0 PRIMARY — leave it off).
  _env_default "$va_env" JARVIS_LOCAL_LLM_FAILOVER 1
  ok "local LLM offline-failover armed (last-resort rung; cloud stays primary)"
}

# livekit.yaml pins key_file at an ABSOLUTE /home/<user> path (committed).
# On any box whose $HOME differs, livekit-server dies at start because the
# key file "doesn't exist". Localize the path in the working tree (no-op
# when it already matches).
localize_livekit_yaml() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  local yaml="$INSTALL_DIR/src/voice-agent/livekit.yaml"
  local want="$HOME/.jarvis/livekit-keys.yaml"
  [ -f "$yaml" ] || return 0
  local current
  current="$(grep -E '^key_file:' "$yaml" | head -1 | sed 's/^key_file:[[:space:]]*//')"
  [ -z "$current" ] && return 0
  if [ "$current" = "$want" ]; then
    ok "livekit.yaml key_file already points at $want"
    return 0
  fi
  sed -i "s|^key_file:.*|key_file: $want|" "$yaml"
  warn "localized livekit.yaml key_file → $want (was $current; dirties the checkout — expected on non-default \$HOME)"
}

# faster-whisper + (GPU-only) the CUDA runtime wheels ctranslate2 needs.
# requirements.txt now carries faster-whisper, but keep this as a detect-
# and-repair net for venvs created before that (and for the nvidia libs,
# which are GPU-conditional and deliberately NOT in requirements.txt).
install_local_stt() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  section "Local STT (faster-whisper)"
  local va="$INSTALL_DIR/src/voice-agent"
  local py="$va/.venv/bin/python"
  if [ ! -x "$py" ]; then
    warn "voice venv missing at $va/.venv — run the voice channel first (skipping STT setup)"
    return 0
  fi

  if "$py" -c "import faster_whisper" 2>/dev/null; then
    ok "faster-whisper already installed ($("$py" -c 'import faster_whisper; print(faster_whisper.__version__)' 2>/dev/null))"
  else
    sub "installing faster-whisper into the voice venv..."
    if _venv_pip_install "faster-whisper~=1.2"; then
      ok "faster-whisper installed"
    else
      warn "faster-whisper install FAILED — on-device STT won't work; re-run ./install.sh to retry"
      return 0
    fi
  fi

  # GPU boxes: ctranslate2 needs cuBLAS + cuDNN 9 at runtime. The pip
  # wheels are the supported delivery (CT2 auto-loads them from the venv —
  # see the note in src/voice-agent/.env). ~700 MB, GPU-gated.
  if detect_nvidia_gpu; then
    if "$py" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('nvidia.cublas') and importlib.util.find_spec('nvidia.cudnn') else 1)" 2>/dev/null; then
      ok "CUDA runtime wheels (nvidia-cublas-cu12 + nvidia-cudnn-cu12) present"
    else
      sub "GPU detected — installing CUDA runtime wheels for faster-whisper (~700 MB)..."
      if _venv_pip_install nvidia-cublas-cu12 nvidia-cudnn-cu12; then
        ok "installed nvidia-cublas-cu12 + nvidia-cudnn-cu12"
      else
        warn "CUDA wheel install failed — GPU STT will fall over; retry with:"
        sub "$va/.venv/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
      fi
    fi
  else
    sub "no GPU — skipping CUDA runtime wheels (CPU STT needs none)"
  fi
}

# Prefetch the STT model so the FIRST utterance doesn't stall for minutes
# behind a ~1.5 GB HuggingFace download. Skipped by JARVIS_SKIP_MODELS=1;
# the model still auto-downloads lazily on first use if skipped/failed.
prefetch_stt_model() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  if [ "${JARVIS_SKIP_MODELS:-0}" = "1" ]; then
    warn "skipping STT model prefetch (JARVIS_SKIP_MODELS=1) — first utterance will trigger the ~1.5 GB download"
    return 0
  fi
  local va="$INSTALL_DIR/src/voice-agent"
  local py="$va/.venv/bin/python"
  [ -x "$py" ] || return 0
  local model
  model="$(_env_get "$va/.env" JARVIS_LOCAL_STT_MODEL)"
  model="${model:-large-v3-turbo}"
  if _stt_model_cached "$model"; then
    ok "STT model '$model' already in HuggingFace cache"
    return 0
  fi
  section "Prefetching STT model '$model' (~1.5 GB; skip with JARVIS_SKIP_MODELS=1)"
  if "$py" -c "import sys; from faster_whisper.utils import download_model; download_model(sys.argv[1])" "$model"; then
    ok "STT model '$model' downloaded"
  else
    warn "STT model prefetch failed (non-fatal — it auto-downloads on first use, which will stall the first utterance)"
  fi
}

# Kokoro TTS — the on-device primary voice. Runs as the `kokoro-tts`
# Docker container (ghcr.io/remsky/kokoro-fastapi-cpu) serving an
# OpenAI-compatible API on 127.0.0.1:8880. Without it, Edge-TTS (cloud,
# auth-free) carries speech — voice still works, just not offline.
install_kokoro_tts() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  if [ "${JARVIS_SKIP_KOKORO:-0}" = "1" ]; then
    warn "skipping Kokoro TTS (JARVIS_SKIP_KOKORO=1) — Edge-TTS (cloud) will carry speech"
    return 0
  fi
  section "Local TTS (Kokoro)"
  local va_env="$INSTALL_DIR/src/voice-agent/.env"
  local url
  url="$(_env_get "$va_env" JARVIS_LOCAL_TTS_URL)"
  url="${url:-http://127.0.0.1:8880/v1}"

  if curl -fsS --max-time 3 -o /dev/null "${url%/}/audio/voices" 2>/dev/null; then
    ok "Kokoro TTS already serving at $url"
    return 0
  fi

  case "$url" in
    http://127.0.0.1:8880*|http://localhost:8880*) : ;;
    *)
      warn "JARVIS_LOCAL_TTS_URL points at $url but nothing answered — not managing a remote TTS box; Edge-TTS carries speech meanwhile"
      return 0 ;;
  esac

  if ! have docker; then
    warn "docker not installed — can't run the Kokoro TTS container. Edge-TTS (cloud) carries speech."
    sub "To go on-device later: install docker, then:"
    sub "docker run -d --name kokoro-tts --restart unless-stopped -p 127.0.0.1:8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest"
    return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    warn "docker installed but the daemon is unreachable (permissions? not running?) — Edge-TTS carries speech."
    sub "Fix docker access, then re-run ./install.sh (or run the kokoro-tts container manually)."
    return 0
  fi

  # Container exists but stopped → just start it (never recreate/overwrite).
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx kokoro-tts; then
    if docker start kokoro-tts >/dev/null 2>&1; then
      ok "started existing kokoro-tts container"
    else
      warn "could not start existing kokoro-tts container — check 'docker logs kokoro-tts'"
    fi
    return 0
  fi

  sub "pulling + starting kokoro-fastapi (CPU image; multi-GB pull on first run)..."
  if docker run -d --name kokoro-tts --restart unless-stopped \
       -p 127.0.0.1:8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest >/dev/null; then
    ok "kokoro-tts container running (127.0.0.1:8880, restart=unless-stopped)"
  else
    warn "kokoro-tts container failed to start (non-fatal — Edge-TTS carries speech). Retry:"
    sub "docker run -d --name kokoro-tts --restart unless-stopped -p 127.0.0.1:8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest"
  fi
}

# Vosk small-en model (~40 MB) — powers the partial-word barge-in tap
# (pipeline/bargein_tap.py). Soft dependency: missing model just disables
# mid-utterance barge-in, so failures here are warn-only.
fetch_vosk_model() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  if [ "${JARVIS_SKIP_MODELS:-0}" = "1" ]; then return 0; fi
  local dst="${JARVIS_PARTIAL_BARGEIN_MODEL:-$HOME/.jarvis/models/vosk-small-en}"
  if [ -d "$dst" ]; then
    ok "vosk barge-in model present at $dst"
    return 0
  fi
  sub "fetching vosk small-en model (~40 MB) for partial-word barge-in..."
  local url="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
  local tmp
  tmp="$(mktemp -d)"
  if curl -fsSL --max-time 600 -o "$tmp/vosk.zip" "$url" \
     && python3 -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$tmp/vosk.zip" "$tmp" \
     && [ -d "$tmp/vosk-model-small-en-us-0.15" ]; then
    mkdir -p "$(dirname "$dst")"
    mv "$tmp/vosk-model-small-en-us-0.15" "$dst"
    ok "vosk model installed at $dst"
  else
    warn "vosk model fetch failed (non-fatal — partial-word barge-in disabled; VAD barge-in still works)"
    sub "Manual: unzip vosk-model-small-en-us-0.15 from https://alphacephei.com/vosk/models to $dst"
  fi
  rm -rf "$tmp"
}

# ── Local LLM (Ollama) — offline-parity failover model ──────────────────
# Stage 1 of full offline parity (2026-07-10): the voice cascade appends the
# local model as its LAST FallbackAdapter rung (JARVIS_LOCAL_LLM_FAILOVER=1 in
# src/voice-agent/.env; providers/llm.py::wrap_local_failover), reached only
# when every cloud rung fails. These provision the engine + model that rung
# needs. Idempotent, warn-not-abort: a missing ollama/model just means no
# offline failover — cloud voice is unaffected.

# _ollama_base_url — the native Ollama API base (strip the /v1 OpenAI-compat
# suffix the voice .env carries).
_ollama_base_url() {
  local url
  url="$(_env_get "$INSTALL_DIR/src/voice-agent/.env" JARVIS_LOCAL_LLM_URL)"
  url="${url:-http://127.0.0.1:11434/v1}"
  url="${url%/}"; url="${url%/v1}"
  printf '%s' "$url"
}

_ollama_api_up() {
  curl -fsS --max-time 3 -o /dev/null "$(_ollama_base_url)/api/tags" 2>/dev/null
}

install_ollama() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  section "Local LLM engine (Ollama)"
  if have ollama; then
    ok "ollama already installed ($(ollama --version 2>/dev/null | head -1))"
  else
    if [ "$(uname -s)" != "Linux" ]; then
      warn "ollama auto-install is Linux-only — install manually from https://ollama.com/download (offline LLM failover stays dormant)"
      return 0
    fi
    # The official installer escalates via sudo internally. Guard the
    # non-interactive + no-NOPASSWD case so a curl-pipe install can't hang
    # or die mid-script — warn and continue instead.
    if ! sudo -n true 2>/dev/null && ! _interactive; then
      warn "ollama not installed and sudo needs a password in a non-interactive run — skipping (later: curl -fsSL https://ollama.com/install.sh | sh)"
      return 0
    fi
    sub "installing ollama (official installer, needs sudo)..."
    if curl -fsSL https://ollama.com/install.sh | sh; then
      ok "ollama installed"
    else
      warn "ollama install FAILED (non-fatal — no offline LLM failover; cloud voice unaffected). Retry: curl -fsSL https://ollama.com/install.sh | sh"
      return 0
    fi
  fi

  # Daemon reachable? The official installer enables a system ollama.service;
  # start it when present, else point at `ollama serve`.
  if _ollama_api_up; then
    ok "ollama API answering at $(_ollama_base_url)"
    return 0
  fi
  if have systemctl && systemctl list-unit-files ollama.service --no-legend 2>/dev/null | grep -q ollama; then
    sub "starting ollama.service..."
    if sudo -n systemctl enable --now ollama.service 2>/dev/null; then
      local _i
      for _i in 1 2 3 4 5; do
        _ollama_api_up && break
        sleep 1
      done
      if _ollama_api_up; then
        ok "ollama.service running ($(_ollama_base_url))"
      else
        warn "ollama.service started but the API isn't answering yet at $(_ollama_base_url) — check 'journalctl -u ollama'"
      fi
    else
      warn "could not start ollama.service (sudo not NOPASSWD?) — run: sudo systemctl enable --now ollama"
    fi
  else
    warn "ollama installed but no daemon reachable at $(_ollama_base_url) — start it with 'ollama serve' (or install the systemd unit)"
  fi
}

pull_local_llm() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  if ! have ollama; then return 0; fi   # install_ollama already warned
  local tag
  tag="$(_env_get "$INSTALL_DIR/src/voice-agent/.env" JARVIS_LOCAL_LLM_MODEL)"
  tag="${tag:-qwen3:4b-instruct-2507-q4_K_M}"
  if [ "$tag" = "auto" ] || [ "$tag" = "AUTO" ]; then
    # 'auto' is resolved by the voice agent's hwfit picker at runtime; use
    # the same picker here when the venv exists, else the safe default.
    local py="$INSTALL_DIR/src/voice-agent/.venv/bin/python"
    if [ -x "$py" ]; then
      tag="$(cd "$INSTALL_DIR/src/voice-agent" && "$py" -c "from providers.local_model_picker import resolve_model_tag; print(resolve_model_tag('auto'))" 2>/dev/null)"
    fi
    tag="${tag:-qwen3:4b-instruct-2507-q4_K_M}"
  fi
  if ! _ollama_api_up; then
    warn "ollama API not reachable — can't check/pull '$tag' (offline LLM failover stays dormant; later: ollama pull $tag)"
    return 0
  fi
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qxF "$tag"; then
    ok "local LLM '$tag' already pulled"
    return 0
  fi
  if [ "${JARVIS_SKIP_MODELS:-0}" = "1" ]; then
    warn "skipping local LLM pull (JARVIS_SKIP_MODELS=1) — offline failover stays dormant until: ollama pull $tag"
    return 0
  fi
  section "Pulling local LLM '$tag' (~2.5 GB for qwen3:4b-instruct-2507; skip with JARVIS_SKIP_MODELS=1)"
  if ollama pull "$tag"; then
    ok "local LLM '$tag' pulled"
  else
    warn "ollama pull '$tag' failed (non-fatal — offline LLM failover stays dormant; retry: ollama pull $tag)"
  fi
}

# ── Voice-stack readiness report (read-only; end of install) ─────────────
verify_voice_stack() {
  if [ "${JARVIS_SKIP_VOICE:-0}" = "1" ]; then return 0; fi
  section "Voice stack readiness"
  local va="$INSTALL_DIR/src/voice-agent"
  local py="$va/.venv/bin/python"
  local issues=0

  # GPU
  if detect_nvidia_gpu; then
    ok "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
    if [ -x "$py" ]; then
      local cuda_n
      cuda_n="$("$py" -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())" 2>/dev/null || echo "?")"
      if [ "$cuda_n" != "?" ] && [ "$cuda_n" -ge 1 ] 2>/dev/null; then
        ok "CUDA usable from the voice venv (ctranslate2 sees $cuda_n device(s))"
      else
        warn "ctranslate2 can't see CUDA from the venv — GPU STT will fail (check nvidia-cublas-cu12/nvidia-cudnn-cu12; after suspend: bin/jarvis-cuda-recover)"
        issues=$((issues+1))
      fi
    fi
  else
    warn "no NVIDIA GPU — STT runs on CPU (~25 s/clip; voice will be degraded/near-silent)"
    issues=$((issues+1))
  fi

  # STT package + model
  if [ -x "$py" ] && "$py" -c "import faster_whisper" 2>/dev/null; then
    ok "faster-whisper importable in venv"
  else
    warn "faster-whisper NOT importable — no on-device STT"
    issues=$((issues+1))
  fi
  local model
  model="$(_env_get "$va/.env" JARVIS_LOCAL_STT_MODEL)"; model="${model:-large-v3-turbo}"
  if _stt_model_cached "$model"; then
    ok "STT model '$model' cached"
  else
    warn "STT model '$model' not cached — first utterance triggers a ~1.5 GB download"
    issues=$((issues+1))
  fi

  # Voice .env + LiveKit
  if [ -f "$va/.env" ]; then
    ok "voice .env present"
    # The keypair may live in .env OR the single secret store (keys.env
    # overrides .env on collision — see setup_env_template).
    if [ -z "$(_env_get "$va/.env" LIVEKIT_API_KEY)" ] \
       && [ -z "$(_env_get "$HOME/.jarvis/keys.env" LIVEKIT_API_KEY)" ]; then
      warn "LIVEKIT_API_KEY not set in $va/.env or ~/.jarvis/keys.env — agent can't auth to the SFU"
      issues=$((issues+1))
    fi
  else
    warn "voice .env MISSING — jarvis-voice-agent.service will fail (non-optional EnvironmentFile=)"
    issues=$((issues+1))
  fi
  if [ -x "$va/livekit-server.bin" ]; then
    ok "livekit-server.bin present"
  else
    warn "livekit-server.bin missing — no SFU, no voice (re-run ./install.sh)"
    issues=$((issues+1))
  fi
  if [ -s "$HOME/.jarvis/livekit-keys.yaml" ]; then
    ok "LiveKit keys at $HOME/.jarvis/livekit-keys.yaml"
  else
    warn "LiveKit keys missing/empty at $HOME/.jarvis/livekit-keys.yaml — livekit-server can't start"
    issues=$((issues+1))
  fi
  if have systemctl; then
    if systemctl --user is-active --quiet livekit-server.service 2>/dev/null; then
      ok "livekit-server.service ACTIVE (ws://127.0.0.1:7880)"
    elif systemctl --user is-enabled --quiet livekit-server.service 2>/dev/null; then
      sub "livekit-server.service enabled but not started (start it after configuring .env — see summary)"
    else
      warn "livekit-server.service not enabled — voice agent has no SFU"
      issues=$((issues+1))
    fi
  fi

  # TTS
  local tts_url
  tts_url="$(_env_get "$va/.env" JARVIS_LOCAL_TTS_URL)"; tts_url="${tts_url:-http://127.0.0.1:8880/v1}"
  if curl -fsS --max-time 3 -o /dev/null "${tts_url%/}/audio/voices" 2>/dev/null; then
    ok "Kokoro TTS serving at $tts_url"
  else
    warn "Kokoro TTS not reachable at $tts_url — Edge-TTS (cloud) will carry speech"
    issues=$((issues+1))
  fi
  if [ -x "$py" ] && "$py" -c "import edge_tts" 2>/dev/null; then
    ok "edge-tts fallback importable"
  else
    warn "edge-tts NOT importable — no TTS fallback"
    issues=$((issues+1))
  fi

  # Local LLM failover (ollama + model) — the offline-parity tail rung
  local ollama_base llm_tag
  ollama_base="$(_ollama_base_url)"
  llm_tag="$(_env_get "$va/.env" JARVIS_LOCAL_LLM_MODEL)"; llm_tag="${llm_tag:-qwen3:4b-instruct-2507-q4_K_M}"
  if curl -fsS --max-time 3 "$ollama_base/api/tags" 2>/dev/null | grep -qF "\"$llm_tag\""; then
    ok "local LLM failover ready: ollama serving '$llm_tag' at $ollama_base"
  elif _ollama_api_up; then
    warn "ollama reachable at $ollama_base but model '$llm_tag' not pulled — offline LLM failover dormant (ollama pull $llm_tag)"
    issues=$((issues+1))
  else
    warn "ollama not reachable at $ollama_base — no offline LLM failover (cloud voice unaffected)"
    issues=$((issues+1))
  fi

  # Barge-in extras (soft)
  local vosk_dir="${JARVIS_PARTIAL_BARGEIN_MODEL:-$HOME/.jarvis/models/vosk-small-en}"
  if [ -d "$vosk_dir" ]; then
    ok "vosk barge-in model present"
  else
    sub "vosk barge-in model absent (optional — partial-word barge-in disabled)"
  fi

  if [ "$issues" -eq 0 ]; then
    ok "voice stack READY"
  else
    warn "$issues item(s) need attention (see ⚠ lines above) — voice may be degraded until fixed"
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

# ── PipeWire echo-cancel: tuned WebRTC AEC3 (L1) ─────────────────────────
# 2026-05-19 — apply the tuned WebRTC AEC3 echo-cancel config (L1 of the
# 3-layer AEC cascade). The helper writes the tuned aec.args
# (webrtc.extended_filter=true; NS/HPF/AGC OFF — owned by the APM layer,
# no double-DSP), restarts PipeWire, verifies echo-cancel-source returns,
# and restores the backup on failure. Idempotent; honors
# JARVIS_PIPEWIRE_AEC=0. Spec 2026-05-19 §5.1 (webrtc-backend-corrected).
install_echo_cancel_aec() {
  if [ -x "$INSTALL_DIR/bin/jarvis-aec-reload" ]; then
    "$INSTALL_DIR/bin/jarvis-aec-reload" \
      && ok "applied tuned WebRTC echo-cancel config (L1)" \
      || warn "echo-cancel tuning failed (non-fatal; defaults remain)"
  fi
}

# ── Cross-session memory: self-hosted honcho (optional, heavy) ───────────
install_honcho() {
  # Honcho gives JARVIS auto-capturing, semantic cross-session recall (the
  # `recall` tool). OPTIONAL + HEAVY: a Docker stack (api + deriver + pgvector
  # Postgres + redis) plus ongoing OpenAI cost. Off by default — the
  # file-backed memory works without it. Opt in with JARVIS_INSTALL_HONCHO=1,
  # or run setup/honcho/setup-honcho.sh anytime. See setup/honcho/README.md.
  if [ "${JARVIS_INSTALL_HONCHO:-0}" != "1" ]; then
    sub "Skipping honcho cross-session memory (optional, heavy)."
    sub "  Enable later: JARVIS_INSTALL_HONCHO=1 ./install.sh  |  setup/honcho/setup-honcho.sh"
    return 0
  fi
  sub "Setting up self-hosted honcho (cross-session memory)..."
  if JARVIS_REPO="$INSTALL_DIR" bash "$INSTALL_DIR/setup/honcho/setup-honcho.sh"; then
    ok "honcho configured (restart the voice agent to activate recall)"
  else
    warn "honcho setup failed (non-fatal; file-backed memory still works)"
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
  if ! which xdotool >/dev/null 2>&1; then
    echo "  [hint] xdotool not found. Critical for input ops (click/type/key):"
    echo "    sudo apt install -y xdotool"
  fi
}

# ── Channel: Desktop (Tauri) ─────────────────────────────────────────────
install_desktop() {
  if [ "${JARVIS_SKIP_DESKTOP:-0}" = "1" ]; then warn "skipping Desktop (JARVIS_SKIP_DESKTOP=1)"; return; fi
  section "Installing Desktop (Tauri) — first build takes 5–10 min"

  local dt="$INSTALL_DIR/src/voice-agent/desktop-tauri"
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
  local exec_path="$INSTALL_DIR/src/voice-agent/desktop-tauri/src-tauri/target/release/jarvis-desktop"
  # The Tauri default icons (src-tauri/icons/{32x32,128x128,tray}.png)
  # are placeholder Tauri logos from `tauri init` (cyan circle, ~500 B).
  # The actual JARVIS branding is the concentric-rings logo at
  # src-tauri/icons/jarvis-rings-128.png. Reuse it so the app-menu entry
  # matches what JARVIS looks like everywhere else.
  local icon_path="$INSTALL_DIR/src/voice-agent/desktop-tauri/src-tauri/icons/jarvis-rings-128.png"

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
    sub "Build now: (cd $INSTALL_DIR/src/voice-agent/desktop-tauri/src-tauri && cargo build --release)"
  fi

  # Refresh GNOME/KDE/XFCE app cache so the entry appears without logout.
  if have update-desktop-database; then
    update-desktop-database "$apps_dir" >/dev/null 2>&1 \
      && ok "refreshed app menu cache" \
      || warn "could not refresh app menu cache (entry may take a moment to appear)"
  fi
}

# ── .env template ────────────────────────────────────────────────────────
# ── First-run config: interactive API keys + persona ─────────────────────
# Per docs/superpowers/specs/2026-05-31-installer-first-run-config-design.md.
# These live in install.sh (not a separate lib) and are called from main() via
# configure(); they degrade safely to a non-interactive template when no
# terminal is reachable, and stay unit-testable (setup/tests/test_configure.sh
# sources this file). Secrets go to ~/.jarvis/keys.env (the single store).

# _env_get <file> <VAR> — print VAR's value (empty if unset/missing).
_env_get() {
  local file="$1" var="$2"
  [ -f "$file" ] || return 0
  grep -E "^${var}=" "$file" 2>/dev/null | tail -1 | sed "s/^${var}=//"
}

# _env_upsert <file> <VAR> <value> — set VAR=value idempotently (replace ^VAR=
# or append), preserve other lines, create file+dir if missing, chmod 600.
_env_upsert() {
  local file="$1" var="$2" value="$3" tmp
  mkdir -p "$(dirname "$file")"
  [ -f "$file" ] || : > "$file"
  tmp="$(mktemp)"
  grep -v -E "^${var}=" "$file" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$var" "$value" >> "$tmp"
  mv "$tmp" "$file"
  chmod 600 "$file"
}

# Tests inject a fixture file via _JARVIS_TTY; real runs use /dev/tty so even
# `curl | bash` (whose stdin is the script) can still prompt the user.
_tty_path() { printf '%s' "${_JARVIS_TTY:-/dev/tty}"; }

# True iff a terminal is reachable and the user hasn't opted out.
_interactive() {
  [ "${JARVIS_NONINTERACTIVE:-0}" = "1" ] && return 1
  [ "${JARVIS_DRY_RUN:-0}" = "1" ]        && return 1
  [ "${JARVIS_SKIP_SETUP:-0}" = "1" ]     && return 1
  [ -t 0 ] && return 0
  # /dev/tty exists even when headless (cron/docker/systemd): access() passes
  # but `exec 3<...` then fails ENXIO. Probe the real open in a disposable
  # subshell so we take the clean non-interactive branch instead of prompting
  # into defaults while emitting "No such device" noise.
  local tty; tty="$(_tty_path)"
  ( exec 3<"$tty" ) 2>/dev/null || return 1
}

# Open (once) a persistent read fd so sequential prompts advance through the
# input. A fresh `read < "$tty"` re-opens at offset 0 each call — fine for a
# real /dev/tty (non-seekable) but re-reads line 1 of a regular-file fixture.
# _JARVIS_TTY_FD tracks the bound path so a new fixture re-opens.
_tty_open_read() {
  local tty; tty="$(_tty_path)"
  if [ "${_JARVIS_TTY_FD:-}" != "$tty" ]; then
    exec 3<"$tty" 2>/dev/null || { _JARVIS_TTY_FD=""; return 1; }
    _JARVIS_TTY_FD="$tty"
  fi
  return 0
}

# Write the prompt where the user sees it without clobbering the input we read.
# Real /dev/tty (char device) → write to it; regular-file fixture → stderr (so
# we don't truncate the queued answers).
_tty_prompt() {
  local tty; tty="$(_tty_path)"
  if [ -c "$tty" ]; then printf '%s' "$1" > "$tty" 2>/dev/null || printf '%s' "$1" >&2
  else printf '%s' "$1" >&2; fi
}

# _ask <prompt> <default> — echo the answer, or <default> if blank.
_ask() {
  local prompt="$1" default="$2" ans
  _tty_prompt "$prompt"
  if _tty_open_read; then IFS= read -r ans <&3 || ans=""; else ans=""; fi
  printf '%s' "${ans:-$default}"
}

# _ask_secret <prompt> — echo the typed secret without terminal echo.
_ask_secret() {
  local prompt="$1" ans
  _tty_prompt "$prompt"
  if _tty_open_read; then IFS= read -rs ans <&3 || ans=""; else ans=""; fi
  _tty_prompt $'\n'
  printf '%s' "$ans"
}

# _confirm <prompt> <default:Y|N> — return 0 for yes, 1 for no.
_confirm() {
  local prompt="$1" default="${2:-N}" ans
  ans="$(_ask "$prompt" "$default")"
  case "$ans" in [Yy]|[Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}

# _maybe_set_key <label> <VAR> <file> — prompt for one key, write if non-empty,
# guarding against silently replacing an existing value.
_maybe_set_key() {
  local label="$1" var="$2" file="$3" existing val
  existing="$(_env_get "$file" "$var")"
  if [ -n "$existing" ]; then
    _confirm "  $label ($var) already set — replace? [y/N] " N || return 0
  fi
  val="$(_ask_secret "  $label key ($var) [blank=skip]: ")"
  if [ -n "$val" ]; then
    _env_upsert "$file" "$var" "$val"
    ok "$var saved"
  fi
}

configure_api_keys() {
  # Provider keys → the single secret store (~/.jarvis/keys.env). va_env holds
  # only the voice-only Deepgram STT key. root .env stays non-secret config.
  local keys_env="$HOME/.jarvis/keys.env"
  local va_env="$INSTALL_DIR/src/voice-agent/.env"
  # Open the persistent read fd HERE (the non-substitution parent) so each
  # per-prompt `val="$(_ask_secret ...)"` subshell inherits fd 3 + its offset.
  _tty_open_read || true
  sub "API keys — press Enter to skip any provider."

  _maybe_set_key "Anthropic"      ANTHROPIC_API_KEY "$keys_env"
  _maybe_set_key "Deepgram (STT)" DEEPGRAM_API_KEY  "$va_env"

  if _confirm "  Configure more providers (OpenAI/DeepSeek/Google/Kimi)? [y/N] " N; then
    _maybe_set_key "OpenAI"   OPENAI_API_KEY   "$keys_env"
    _maybe_set_key "DeepSeek" DEEPSEEK_API_KEY "$keys_env"
    _maybe_set_key "Google"   GOOGLE_API_KEY   "$keys_env"
    _maybe_set_key "Kimi"     KIMI_API_KEY     "$keys_env"
  fi

  local v has_llm=""
  for v in ANTHROPIC_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY GOOGLE_API_KEY KIMI_API_KEY; do
    [ -n "$(_env_get "$keys_env" "$v")" ] && has_llm=1
  done
  [ -z "$has_llm" ] && warn "No LLM key set — add one to $keys_env before starting the voice agent."
  return 0
}

configure_soul() {
  local soul_src="$INSTALL_DIR/src/voice-agent/prompts/soul.md"
  local soul_dst="$HOME/.jarvis/SOUL.md"
  # Persistent read fd (see configure_api_keys) so this function's sequence of
  # _confirm/_ask prompts advances through the input instead of re-reading line 1.
  _tty_open_read || true
  if [ ! -f "$soul_src" ]; then
    warn "base soul not found at $soul_src — skipping persona setup"; return 0
  fi

  if [ -f "$soul_dst" ]; then
    _confirm "  ~/.jarvis/SOUL.md exists — overwrite to re-personalize? [y/N] " N \
      || { ok "keeping existing $soul_dst"; return 0; }
  else
    _confirm "  Personalize the assistant's persona now? [Y/n] " Y \
      || { sub "skipping persona (JARVIS uses the built-in soul)"; return 0; }
  fi

  mkdir -p "$HOME/.jarvis"
  cp "$soul_src" "$soul_dst"
  chmod 600 "$soul_dst"

  # Optional rename. Only [A-Za-z0-9 _-] accepted (no sed metachar injection).
  local name; name="$(_ask "  Assistant name [JARVIS]: " "JARVIS")"
  if [ -n "$name" ] && [ "$name" != "JARVIS" ]; then
    if printf '%s' "$name" | grep -qE '^[A-Za-z0-9 _-]+$'; then
      sed -i "s/\\bJARVIS\\b/${name}/g" "$soul_dst"
      ok "set assistant name to '$name'"
    else
      warn "name has unsupported characters — keeping 'JARVIS'"
    fi
  fi
  ok "wrote $soul_dst (chmod 600)"

  # Offer the editor for hand tweaks. EDITOR=true (tests) is a no-op.
  local editor="${EDITOR:-}"
  [ -z "$editor" ] && { have nano && editor=nano || editor=vi; }
  if _confirm "  Open $soul_dst in $editor to fine-tune? [Y/n] " Y; then
    "$editor" "$soul_dst" < "$(_tty_path)" > "$(_tty_path)" 2>&1 \
      || warn "editor exited non-zero; $soul_dst left as written"
  fi
  return 0
}

# First-run config entrypoint, called from main(). Interactive → prompt for
# keys + persona; non-interactive (curl|bash, CI, headless) → just write the
# templates and tell the user how to fill them.
configure() {
  if [ "${JARVIS_SKIP_SETUP:-0}" = "1" ]; then
    warn "skipping first-run setup (JARVIS_SKIP_SETUP=1)"
    setup_env_template
    return 0
  fi
  section "First-run configuration"
  if _interactive; then
    setup_env_template      # ensure keys.env + root .env templates exist
    configure_api_keys      # upsert real keys into keys.env + voice-agent/.env
    configure_soul          # optional persona override at ~/.jarvis/SOUL.md
  else
    sub "non-interactive shell — writing the key template; edit it to add keys."
    setup_env_template
    sub "Personalize later: copy src/voice-agent/prompts/soul.md to ~/.jarvis/SOUL.md and edit."
  fi
  return 0
}

setup_env_template() {
  local keys_env="$HOME/.jarvis/keys.env"
  section "API key template"

  # keys.env — the SINGLE secret store. Loaded by the voice agent, the
  # CLI/proxy, and the web app (src/web/next.config.ts), and it OVERRIDES every
  # other env file on collision. All API keys / passwords live here (the Tray UI
  # writes here too). Created with empty placeholders so a fresh install has
  # something to fill. (Provider keys moved out of root .env 2026-06-15.)
  if [ -f "$keys_env" ]; then
    ok "keys.env already exists at $keys_env; not overwriting"
  else
    mkdir -p "$(dirname "$keys_env")"
    cat > "$keys_env" <<'EOF'
# JARVIS — single secret store. Loaded by the voice agent, the CLI/proxy, and
# the web app, and it OVERRIDES every other env file on collision. Keep ALL
# secrets (API keys, passwords) here and nowhere else.

# LLM providers (fill these in with real keys)
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=
KIMI_API_KEY=

# Other secrets (uncomment + set if you use these)
# JARVIS_PG_DSN=postgresql://jarvis:PASSWORD@localhost:5432/jarvis
EOF
    # Lock to 0600 so other local users / containers / web pages can't read the
    # API keys. (Per security review 2026-05-16: previously 0664; keys exposed.)
    chmod 600 "$keys_env"
    ok "created $keys_env (chmod 600 — the single secret store; fill in your real keys)"
  fi

  # root .env — NON-SECRET shared config only (model ids, provider choice,
  # sandbox knobs), loaded by the CLI/proxy and the web app. Secrets do NOT go
  # here; keys.env (above) overrides this file on collision.
  if [ -f "$INSTALL_DIR/.env" ]; then
    ok ".env already exists; not overwriting"
    return
  fi
  cat > "$INSTALL_DIR/.env" <<'EOF'
# JARVIS — non-secret shared config. Loaded by the CLI/proxy and the web app.
# DO NOT put secrets here — API keys / passwords go in ~/.jarvis/keys.env,
# which overrides this file on collision.

# Optional knobs (uncomment + set if you use these)
# JARVIS_PROVIDER=deepseek
# OLLAMA_HOST=http://127.0.0.1:11434

# Sandbox / safety knobs (uncomment to override defaults)
# JARVIS_BASH_BWRAP=0           # disable bash-tool bubblewrap sandbox
#                               # (test / debug only — reduces blast-radius
#                               # protection on ~/.ssh, ~/.aws, ~/.gnupg)
# JARVIS_REQUIRE_LOCAL_AUTH=1   # require Bearer token on bridge + web /api/*
#                               # (default on when start-desktop.sh launches;
#                               # set explicitly if you bypass that script)
# JARVIS_DAILY_COST_CEILING_USD=5  # canary alert threshold (P1-OBS-1)
EOF
  chmod 600 "$INSTALL_DIR/.env"
  ok "created $INSTALL_DIR/.env (chmod 600 — non-secret config; put keys in keys.env)"
}

# ── Final summary ────────────────────────────────────────────────────────
print_summary() {
  section "Done"
  cat <<EOF
  Install location:  $INSTALL_DIR
  CLI launcher:      $LOCAL_BIN/jarvis  (also $LOCAL_BIN/jarvis-desktop)

  Next steps:
    1. Edit $INSTALL_DIR/.env and fill in real API keys.
    2. Start the SFU + voice agent + voice client (in this order —
       voice-agent requires livekit-server, voice-client is the
       desktop's native PortAudio bridge):
         systemctl --user start livekit-server.service
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
         $INSTALL_DIR/src/voice-agent/desktop-tauri/src-tauri/target/release/jarvis-desktop
       (or click 'JARVIS' in your app launcher — Ctrl+Shift+Space toggles
       click-through once it's running)

  Re-run this script anytime to re-install or update a channel.
  Skip channels with JARVIS_SKIP_{CLI,VOICE,DESKTOP,WEB}=1.
EOF
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
  # ── Channel selection (optional) ───────────────────────────────────────
  # No flags (or --all) → install everything. Select specific channels with
  # --cli / --voice / --desktop / --web (the rest are skipped). The
  # JARVIS_SKIP_{CLI,VOICE,DESKTOP,WEB}=1 env vars still work and compose.
  local _all=0 _sel=0 _cli=0 _voice=0 _desktop=0 _web=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --all)     _all=1 ;;
      --cli)     _cli=1; _sel=1 ;;
      --voice)   _voice=1; _sel=1 ;;
      --desktop) _desktop=1; _sel=1 ;;
      --web)     _web=1; _sel=1 ;;
      -h|--help)
        echo "Usage: ./install.sh [--all | --cli --voice --desktop --web]"
        echo "  No flags or --all installs everything (CLI, Voice, Desktop, Web)."
        echo "  Pass one or more channels to install only those; the rest skip."
        echo "  Env: JARVIS_SKIP_{CLI,VOICE,DESKTOP,WEB}=1 also skips a channel."
        exit 0 ;;
      *) warn "ignoring unknown option: $1" ;;
    esac
    shift
  done
  if [ "$_all" = "0" ] && [ "$_sel" = "1" ]; then
    [ "$_cli" = "1" ]     || export JARVIS_SKIP_CLI=1
    [ "$_voice" = "1" ]   || export JARVIS_SKIP_VOICE=1
    [ "$_desktop" = "1" ] || export JARVIS_SKIP_DESKTOP=1
    [ "$_web" = "1" ]     || export JARVIS_SKIP_WEB=1
  fi

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
  ensure_livekit_binary  # fetch livekit-server.bin at install time (not in git)
  setup_voice_env        # src/voice-agent/.env bootstrap + local STT/TTS flags (GPU-aware)
  setup_livekit_keys
  localize_livekit_yaml  # fix hardcoded key_file path on non-default $HOME
  install_local_stt      # faster-whisper + (GPU) CUDA runtime wheels in the venv
  prefetch_stt_model     # ~1.5 GB HF model (skip: JARVIS_SKIP_MODELS=1)
  install_kokoro_tts     # on-device TTS container (skip: JARVIS_SKIP_KOKORO=1)
  fetch_vosk_model       # ~40 MB partial-word barge-in model (skip: JARVIS_SKIP_MODELS=1)
  install_ollama         # local LLM engine for the offline failover rung (warn-not-abort)
  pull_local_llm         # ~4.7 GB failover model (skip: JARVIS_SKIP_MODELS=1)
  check_computer_use_deps  # optional probes for computer_use subagent
  install_audio_profile
  install_echo_cancel_aec
  configure                # first-run: interactive API keys + persona, or just the templates when non-interactive
  install_honcho           # optional: self-hosted honcho cross-session memory (JARVIS_INSTALL_HONCHO=1)
  verify_voice_stack       # read-only readiness report: GPU / STT / SFU / TTS
  print_summary
}

# Only run the installer when executed directly. Sourcing it (e.g. the test
# suite in setup/tests/, which loads these functions against throwaway temp
# dirs) must NOT run main() — otherwise sourcing would clone, build, and enable
# systemd services on a live box.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  main "$@"
fi
