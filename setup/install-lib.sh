# JARVIS installer function library.
# Sourced by install.sh (the thin bootstrap). Contains all installer
# function definitions. Safe to source — no code executes at source time
# unless the guard at the bottom matches (direct execution).
#
# Functions defined here:
#   detect_os, _resolve_paths, _pkg_mgr_cmd, detect_fhs,
#   _env_get, _env_upsert, _tty_*, _interactive, _ask*, _confirm,
#   configure_api_keys, configure_soul, configure, check_prereqs,
#   check_network_prerequisites, clone_or_update,
#   install_cli, _write_launcher_shim, install_node,
#   install_web, _install_voice_deps_tiered, install_voice_agent,
#   install_playwright_chromium, copy_config_templates,
#   maybe_start_services, install_systemd_units, install_bubblewrap,
#   install_system_packages, generate_bridge_token, ensure_livekit_binary,
#   setup_livekit_keys, check_computer_use_deps, check_desktop_prereqs,
#   install_audio_profile, install_echo_cancel_aec, install_desktop,
#   install_desktop_entry, setup_env_template, print_summary,
#   main, ensure_mode, setup_mode, postinstall_mode, _entry_route

detect_os() {
  case "$(uname -s)" in
    Linux) OS=linux ;;
    *)     OS=unknown ;;
  esac
  DISTRO=unknown

  # Prefer /etc/os-release (POSIX ID + ID_LIKE fields).
  if [ -f /etc/os-release ]; then
    local id id_like
    id="$(grep -E '^ID=' /etc/os-release | sed 's/ID=//;s/"//g' 2>/dev/null || echo "")"
    id_like="$(grep -E '^ID_LIKE=' /etc/os-release | sed 's/ID_LIKE=//;s/"//g' 2>/dev/null || echo "")"
    # Map to canonical distro name for package manager selection.
    case "$id" in
      ubuntu|debian|linuxmint|pop|elementary|zorin|kali) DISTRO=debian ;;
      fedora|rhel|centos|rocky|alma)                     DISTRO=fedora  ;;
      arch|manjaro|endeavouros|garuda|artix|archlinux)   DISTRO=arch    ;;
      alpine)                                            DISTRO=alpine   ;;
      opensuse*|sles|suse)                               DISTRO=opensuse ;;
      nixos)                                             DISTRO=nixos    ;;
      *)                                                 DISTRO="$id"    ;;
    esac
    # Fall back to ID_LIKE if ID didn't match canonical list.
    if [ "$DISTRO" = "unknown" ] || [ "$DISTRO" != "$id" ]; then
      case " $id_like " in
        *"debian"*)  DISTRO=debian  ;;
        *"fedora"*)  DISTRO=fedora  ;;
        *"rhel"*)    DISTRO=fedora  ;;
        *"arch"*)    DISTRO=arch    ;;
      esac
    fi
  else
    # Last resort: probe package managers by availability.
    if have apt-get; then   DISTRO=debian
    elif have dnf; then     DISTRO=fedora
    elif have pacman; then  DISTRO=arch
    elif have apk; then     DISTRO=alpine
    elif have zypper; then  DISTRO=opensuse
    fi
  fi
  sub "detected OS: $OS, distro: $DISTRO"
}

# ── Lazy path resolution ──────────────────────────────────────────────
# Set default paths based on $HOME if detect_fhs() hasn't run yet. This
# allows the test suite to source install.sh and override $HOME afterward.
_resolve_paths() {
  [ -z "${INSTALL_DIR:-}" ] && INSTALL_DIR="${JARVIS_INSTALL_DIR:-$HOME/Documents/Projects/jarvis}"
  [ -z "${LOCAL_BIN:-}" ]   && LOCAL_BIN="$HOME/.local/bin"
  [ -z "${JARVIS_HOME:-}" ] && JARVIS_HOME="$HOME/.jarvis"
  [ -z "${JARVIS_LOG_DIR:-}" ] && JARVIS_LOG_DIR="$HOME/.local/share/jarvis/logs"
  [ -z "${JARVIS_DATA_DIR:-}" ] && JARVIS_DATA_DIR="$HOME/.local/share/jarvis"
  [ -z "${SYSTEMD_DIR:-}" ] && SYSTEMD_DIR="$HOME/.config/systemd/user"
  [ -z "${SYSTEMD_SCOPE:-}" ] && SYSTEMD_SCOPE="user"
  [ -z "${VA_ENV:-}" ] && VA_ENV="$INSTALL_DIR/src/voice-agent/.env"
}

# _pkg_mgr_cmd — echo the package-manager install command for $DISTRO.
# Only verb + packages; caller prepends sudo.
_pkg_mgr_cmd() {
  case "$DISTRO" in
    debian)   echo "DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y" ;;
    fedora)   echo "dnf install -y" ;;
    arch)     echo "pacman -S --noconfirm" ;;
    alpine)   echo "apk add" ;;
    opensuse) echo "zypper install -y" ;;
    *)        echo "" ;;
  esac
}

# ── FHS root layout detection ──────────────────────────────────────────
# When running as root (EUID 0) or JARVIS_FHS=1, install to system paths
# with system-scoped systemd services instead of user-space paths.
detect_fhs() {
  _resolve_paths
  JARVIS_FHS="${JARVIS_FHS:-0}"
  [ "$EUID" = "0" ] && JARVIS_FHS=1

  if [ "$JARVIS_FHS" = "1" ]; then
    INSTALL_DIR="/opt/jarvis"
    LOCAL_BIN="/usr/local/bin"
    JARVIS_HOME="/var/lib/jarvis"
    JARVIS_LOG_DIR="/var/log/jarvis"
    JARVIS_DATA_DIR="/var/lib/jarvis"
    SYSTEMD_DIR="/etc/systemd/system"
    SYSTEMD_SCOPE="system"
    VA_ENV="/etc/jarvis/voice-agent.env"
    sub "FHS root layout enabled — installing to system paths"
  else
    INSTALL_DIR="${JARVIS_INSTALL_DIR:-$HOME/Documents/Projects/jarvis}"
    LOCAL_BIN="$HOME/.local/bin"
    JARVIS_HOME="$HOME/.jarvis"
    JARVIS_LOG_DIR="$HOME/.local/share/jarvis/logs"
    JARVIS_DATA_DIR="$HOME/.local/share/jarvis"
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    SYSTEMD_SCOPE="user"
    VA_ENV="$INSTALL_DIR/src/voice-agent/.env"
  fi
  sub "install dir: $INSTALL_DIR"
  sub "JARVIS home: $JARVIS_HOME"
}

# ── .env read/write helpers ──────────────────────────────────────────
# _env_get <file> <VAR> — print the current value of VAR (empty if unset/missing).
_env_get() {
  local file="$1" var="$2"
  [ -f "$file" ] || return 0
  grep -E "^${var}=" "$file" 2>/dev/null | tail -1 | sed "s/^${var}=//"
}

# _env_upsert <file> <VAR> <value> — set VAR=value idempotently. Replaces an
# existing `^VAR=` line (any value) or appends; preserves all other lines;
# creates the file + parent dir if missing; chmod 600.
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

# ── Interactivity + prompts ──────────────────────────────────────────
# Tests inject a fixture file via _JARVIS_TTY; real runs use /dev/tty so even
# `curl | bash` (whose stdin is the script) can prompt the user.
_tty_path() { printf '%s' "${_JARVIS_TTY:-/dev/tty}"; }

# True iff a terminal is reachable and the user hasn't opted out.
_interactive() {
  [ "${JARVIS_NONINTERACTIVE:-0}" = "1" ] && return 1
  [ "${JARVIS_DRY_RUN:-0}" = "1" ]        && return 1
  [ "${JARVIS_SKIP_SETUP:-0}" = "1" ]     && return 1
  [ -t 0 ] && return 0
  # The device node /dev/tty exists even in a genuinely headless context
  # (cron/docker/systemd), and `[ -r /dev/tty ]` passes access() but `exec 3<...`
  # then fails with ENXIO because there is no controlling terminal. Probe
  # with a disposable subshell that tries the actual open — if it fails we take
  # the clean non-interactive branch rather than false-positiving into prompts
  # that will no-op to defaults while emitting scary "No such device" noise.
  local tty; tty="$(_tty_path)"
  ( exec 3<"$tty" ) 2>/dev/null || return 1
}

# Open (once) a persistent read fd on the tty path so sequential prompts
# advance through the input. A fresh `read < "$tty"` re-opens the path at
# offset 0 every call, which works for a real /dev/tty (a non-seekable
# stream) but re-reads the SAME first line of a regular-file test fixture —
# so multi-prompt flows would read the first answer over and over. Binding
# one fd keeps the read cursor where it left off for both. _JARVIS_TTY_FD
# tracks which path the fd is bound to so a new fixture (new path) re-opens.
_tty_open_read() {
  local tty; tty="$(_tty_path)"
  if [ "${_JARVIS_TTY_FD:-}" != "$tty" ]; then
    exec 3<"$tty" 2>/dev/null || { _JARVIS_TTY_FD=""; return 1; }
    _JARVIS_TTY_FD="$tty"
  fi
  return 0
}

# Write the prompt where the user will see it WITHOUT clobbering the input
# we read from. For a real /dev/tty (char device) we write to the tty; for a
# regular-file test fixture (which holds the queued answers) writing to it
# would truncate/corrupt the answers, so we send the prompt to stderr instead.
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
  _resolve_paths
  local root_env="$INSTALL_DIR/.env"
  local va_env="$INSTALL_DIR/src/voice-agent/.env"
  # Open the persistent read fd HERE (the non-substitution parent) so the
  # per-prompt `val="$(_ask_secret ...)"` subshells inherit fd 3 and share
  # its file offset — without this each subshell would reopen at offset 0.
  _tty_open_read || true
  sub "API keys — press Enter to skip any provider."

  _maybe_set_key "Anthropic"      ANTHROPIC_API_KEY "$root_env"
  _maybe_set_key "Groq"           GROQ_API_KEY      "$root_env"
  _maybe_set_key "Deepgram (STT)" DEEPGRAM_API_KEY  "$va_env"

  if _confirm "  Configure more providers (OpenAI/DeepSeek/Google/Kimi)? [y/N] " N; then
    _maybe_set_key "OpenAI"   OPENAI_API_KEY   "$root_env"
    _maybe_set_key "DeepSeek" DEEPSEEK_API_KEY "$root_env"
    _maybe_set_key "Google"   GOOGLE_API_KEY   "$root_env"
    _maybe_set_key "Kimi"     KIMI_API_KEY     "$root_env"
  fi

  local v has_llm=""
  for v in ANTHROPIC_API_KEY GROQ_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY GOOGLE_API_KEY KIMI_API_KEY; do
    [ -n "$(_env_get "$root_env" "$v")" ] && has_llm=1
  done
  [ -z "$has_llm" ] && warn "No LLM key set — add one to $root_env before starting the voice agent."
  return 0
}

configure_soul() {
  _resolve_paths
  local soul_src="$INSTALL_DIR/src/voice-agent/prompts/soul.md"
  local soul_dst="$JARVIS_HOME/SOUL.md"
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

  mkdir -p "$JARVIS_HOME"
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

configure() {
  _resolve_paths
  if [ "${JARVIS_SKIP_SETUP:-0}" = "1" ]; then
    warn "skipping first-run setup (JARVIS_SKIP_SETUP=1)"
    setup_env_template
    return 0
  fi
  section "First-run configuration"
  if _interactive; then
    setup_env_template      # ensure .env exists (template + optional-knob comments)
    configure_api_keys      # upsert real keys into .env + voice-agent/.env
    configure_soul          # optional persona override at ~/.jarvis/SOUL.md
  else
    sub "non-interactive shell — writing the key template; edit it to add keys."
    setup_env_template
    sub "Personalize later: copy src/voice-agent/prompts/soul.md to ~/.jarvis/SOUL.md and edit."
  fi
  return 0
}

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

  # Node + npm — voice/web/desktop need them. If missing, install_node()
  # will download a managed tarball later. Non-fatal here so the install
  # continues and install_node() gets a chance to run.
  if have node; then ok "node ($(node --version))"; else warn "node not found — will attempt managed download"; fi
  if have npm;  then ok "npm  ($(npm --version))";  else warn "npm not found — will download with node"; fi

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

# ── Network prerequisite check ───────────────────────────────────────────
# Probe key endpoints before the install starts so we fail fast (instead of
# hanging at "Installing Voice Agent" with no network).
check_network_prerequisites() {
  section "Checking network connectivity"
  local ok=0 total=0
  _probe_url() {
    local url="$1" label="$2"
    total=$((total + 1))
    if curl -fsSL --max-time 5 -o /dev/null "$url" 2>/dev/null; then
      ok=$((ok + 1))
    else
      warn "$label ($url) unreachable — install may fail"
    fi
  }
  _probe_url "https://github.com"          "GitHub"
  _probe_url "https://pypi.org"            "PyPI"
  _probe_url "https://registry.npmjs.org"  "npm registry"
  _probe_url "https://astral.sh"           "Astral (uv)"
  if [ "$ok" = "$total" ]; then
    ok "all $total endpoints reachable"
  else
    warn "$ok/$total endpoints reachable — continuing but some channels may fail"
  fi
}

# ── Clone (or update) ────────────────────────────────────────────────────
clone_or_update() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    section "Updating existing checkout"
    local branch
    branch="$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")"
    git -C "$INSTALL_DIR" fetch --quiet origin "$branch"

    # Stash local changes before pulling so --ff-only doesn't fail on
    # uncommitted work. Inspired by Hermes Agent's auto-stash flow.
    local stash_ref=""
    if ! git -C "$INSTALL_DIR" diff --quiet 2>/dev/null; then
      local stash_name="jarvis-install-autostash-$(date +%Y%m%d-%H%M%S)"
      git -C "$INSTALL_DIR" stash push --include-untracked -m "$stash_name" >/dev/null 2>&1 && stash_ref="stash@{0}"
      sub "stashed local changes as '$stash_name'"
    fi

    git -C "$INSTALL_DIR" pull --ff-only origin "$branch" || {
      warn "pull --ff-only failed (merge conflict?); leaving checkout as-is"
      # On failure, still try to restore stash if one was created.
      if [ -n "$stash_ref" ]; then
        warn "stash '$stash_name' preserved — run: git -C $INSTALL_DIR stash apply"
      fi
      return 0
    }
    ok "checkout at $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"

    # Offer to restore stashed changes.
    if [ -n "$stash_ref" ] && _interactive; then
      if _confirm "  Restore local changes that were stashed? [Y/n] " Y; then
        if git -C "$INSTALL_DIR" stash apply >/dev/null 2>&1; then
          git -C "$INSTALL_DIR" stash drop >/dev/null 2>&1 || true
          ok "local changes restored"
        else
          warn "stash apply failed (conflict); stash preserved as '$stash_name'"
          warn "  git -C $INSTALL_DIR stash drop  # after resolving"
        fi
      else
        warn "stash preserved as '$stash_name' — run: git -C $INSTALL_DIR stash apply to restore"
      fi
    elif [ -n "$stash_ref" ]; then
      warn "non-interactive — stash preserved as '$stash_name'"
      warn "  git -C $INSTALL_DIR stash apply   # restore when ready"
    fi
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

  # Launcher shim (env-sanitizing wrapper) — replaces a bare symlink so a
  # pre-set PYTHONPATH / PYTHONHOME can't force the CLI to import from a
  # different checkout or venv. Inspired by Hermes Agent's approach.
  _write_launcher_shim "$LOCAL_BIN/jarvis" "$INSTALL_DIR/bin/jarvis"

  # Desktop launcher shim — same env sanitization for the desktop launcher.
  _write_launcher_shim "$LOCAL_BIN/jarvis-desktop" "$INSTALL_DIR/bin/jarvis-desktop"

  # Setup-wizard launcher shim — re-runnable config without reinstalling.
  _write_launcher_shim "$LOCAL_BIN/jarvis-setup" "$INSTALL_DIR/bin/jarvis-setup"

  ok "deps installed"
  ok "launcher shim at $LOCAL_BIN/jarvis → $INSTALL_DIR/bin/jarvis"
  ok "launcher shim at $LOCAL_BIN/jarvis-desktop → $INSTALL_DIR/bin/jarvis-desktop"
  ok "launcher shim at $LOCAL_BIN/jarvis-setup → $INSTALL_DIR/bin/jarvis-setup"

  # Shell config injection — auto-add ~/.local/bin to PATH in the user's
  # shell rc file. Silent; no prompts. Inspired by Hermes Agent's setup_path().
  case ":$PATH:" in
    *":$LOCAL_BIN:"*) : ;;
    *)
      if [ -n "${SHELL:-}" ]; then
        local rc_file=""
        case "${SHELL##*/}" in
          zsh)  rc_file="$HOME/.zshrc" ;;
          bash) rc_file="$HOME/.bashrc" ;;
          fish) rc_file="$HOME/.config/fish/config.fish" ;;
          *)    rc_file="$HOME/.profile" ;;
        esac
        if [ -n "$rc_file" ]; then
          mkdir -p "$(dirname "$rc_file")"
          if [ ! -f "$rc_file" ]; then
            printf '%s\n' "# JARVIS launcher path" "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$rc_file"
            ok "created $rc_file with PATH entry for $LOCAL_BIN"
          elif ! grep -q '\.local/bin' "$rc_file" 2>/dev/null; then
            printf '\n%s\n' "# JARVIS launcher path" "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$rc_file"
            ok "appended PATH entry to $rc_file"
          fi
        fi
      fi
      ;;
  esac
}

# _write_launcher_shim <path> <real-target> — create an env-sanitizing
# wrapper script that clears PYTHONPATH/PYTHONHOME before exec'ing the
# real binary. Prevents cross-checkout pollution and uv config leaks
# (inspired by Hermes Agent's approach).
_write_launcher_shim() {
  local path="$1" real_target="$2"
  cat > "$path" <<EOF
#!/usr/bin/env bash
# JARVIS launcher shim — generated by install.sh, do not edit.
# Sanitizes inherited environment to prevent cross-checkout pollution,
# module shadowing from PYTHONPATH, and uv config leaks.
set -euo pipefail
unset PYTHONPATH PYTHONHOME
export UV_NO_CONFIG=1
exec "$real_target" "\$@"
EOF
  chmod 755 "$path"
}

# ── Managed Node.js download ───────────────────────────────────────────
# Download Node.js v22 binary tarball if the system has no node. The
# desktop Tauri build and web channel need npm + node to run. Uses the
# same approach as Hermes Agent's install_node().
install_node() {
  if have node; then
    ok "node ($(node --version)) already on PATH"
    return 0
  fi

  section "Installing Node.js (managed download)"

  local arch
  case "$(uname -m)" in
    x86_64)        arch="x64"    ;;
    aarch64|arm64) arch="arm64"  ;;
    *)             warn "unsupported arch $(uname -m) for managed Node.js; install manually"; return 1 ;;
  esac

  # Resolve latest v22.x.x tarball name from the index page.
  local index_url="https://nodejs.org/dist/latest-v${NODE_VERSION}.x/"
  local tarball_name
  tarball_name="$(curl -fsSL "$index_url" 2>/dev/null | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-linux-${arch}\.tar\.xz" | head -1)"
  [ -z "$tarball_name" ] && tarball_name="$(curl -fsSL "$index_url" 2>/dev/null | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-linux-${arch}\.tar\.gz" | head -1)"

  if [ -z "$tarball_name" ]; then
    warn "could not find Node.js v${NODE_VERSION} binary for linux-${arch}"
    warn "install Node.js >= 20 manually: https://nodejs.org/en/download/"
    return 1
  fi

  local tmp_dir; tmp_dir="$(mktemp -d)"
  sub "downloading $tarball_name..."
  if ! curl -fsSL "${index_url}${tarball_name}" -o "$tmp_dir/$tarball_name"; then
    warn "download failed — install Node.js >= 20 manually"
    rm -rf "$tmp_dir"
    return 1
  fi

  sub "extracting..."
  if echo "$tarball_name" | grep -q '\.tar\.xz$'; then
    tar xf "$tmp_dir/$tarball_name" -C "$tmp_dir" 2>/dev/null
  else
    tar xzf "$tmp_dir/$tarball_name" -C "$tmp_dir" 2>/dev/null
  fi

  local extracted; extracted="$(ls -d "$tmp_dir"/node-v* 2>/dev/null | head -1)"
  if [ ! -d "$extracted" ]; then
    warn "extraction failed — install Node.js >= 20 manually"
    rm -rf "$tmp_dir"
    return 1
  fi

  # Place into JARVIS_HOME/node/ and symlink binaries to ~/.local/bin/.
  rm -rf "$JARVIS_HOME/node"
  mkdir -p "$JARVIS_HOME"
  mv "$extracted" "$JARVIS_HOME/node"
  rm -rf "$tmp_dir"

  mkdir -p "$LOCAL_BIN"
  ln -sf "$JARVIS_HOME/node/bin/node" "$LOCAL_BIN/node"
  ln -sf "$JARVIS_HOME/node/bin/npm"  "$LOCAL_BIN/npm"
  ln -sf "$JARVIS_HOME/node/bin/npx"  "$LOCAL_BIN/npx"
  export PATH="$LOCAL_BIN:$PATH"

  ok "Node.js $("$LOCAL_BIN/node" --version) installed to $JARVIS_HOME/node/"
  ok "symlinked node/npm/npx to $LOCAL_BIN"
}

# ── Channel: Web (Next.js) ───────────────────────────────────────────────
install_web() {
  if [ "${JARVIS_SKIP_WEB:-0}" = "1" ]; then warn "skipping Web (JARVIS_SKIP_WEB=1)"; return; fi
  section "Installing Web (Next.js)"
  (cd "$INSTALL_DIR/src/web" && bun install --silent)
  ok "deps installed — run 'cd $INSTALL_DIR/src/web && bun dev' to start dev server"
}

# ── Channel: Voice agent ─────────────────────────────────────────────────
# Uses uv (Astral) for Python install + venv + dependency sync. uv is
# 10-100x faster than pip for cold resolves, handles the Python install
# itself if absent, and matches the Windows installer (install.ps1) so
# both platforms run the same package manager. Falls back to system
# python3 + pip when uv is unavailable or the user opts out via
# JARVIS_NO_UV=1.
# ── Multi-tier pip install ──────────────────────────────────────────────
# Graduate through increasingly forgiving strategies so a single broken or
# compromised dep doesn't brick the whole voice-agent install.
# Usage: _install_voice_deps_tiered <venv-dir> [no-uv]
_install_voice_deps_tiered() {
  local va="$1" no_uv="${2:-}" pip_cmd py_cmd tier
  if [ "$no_uv" = "no-uv" ]; then
    pip_cmd="$va/.venv/bin/pip"
    py_cmd="$va/.venv/bin/python"
  else
    pip_cmd="VIRTUAL_ENV=$va/.venv UV_PROJECT_ENVIRONMENT=$va/.venv uv pip"
    py_cmd="$va/.venv/bin/python"
  fi

  local req="$va/requirements.txt"
  [ -f "$req" ] || { warn "no requirements.txt at $req — skipping dep install"; return 0; }

  # Tier 0: uv sync (hash-verified via lockfile if it exists)
  if [ "$no_uv" != "no-uv" ] && [ -f "$va/uv.lock" ]; then
    if VIRTUAL_ENV="$va/.venv" UV_PROJECT_ENVIRONMENT="$va/.venv" \
         uv sync --locked --no-dev >/dev/null 2>&1; then
      ok "deps installed via uv sync (lockfile-verified)"
      return 0
    fi
    warn "uv sync failed — falling back to uv pip install"
  fi

  # Tier 1: uv pip install (or pip) with full requirements.txt
  if eval "$pip_cmd install --quiet -r \"$req\"" 2>/dev/null; then
    ok "deps installed via pip (full requirements.txt)"
    return 0
  fi
  warn "Tier 1 (full) failed — trying filtered install"

  # Tier 2: pip install --no-deps of core packages only (extract top-level names)
  # Parse requirements.txt for package names, skip known-heavy extras that
  # might be the broken ones.
  local core_req="${TMPDIR:-/tmp}/jarvis-core-reqs-$$.txt"
  grep -E '^[a-zA-Z0-9_][a-zA-Z0-9_.-]*' "$req" \
    | grep -v -E '(livekit|torch|tensorflow|whisper|transformers)' \
    > "$core_req" 2>/dev/null || true
  if [ -s "$core_req" ]; then
    if eval "$pip_cmd install --quiet --no-deps -r \"$core_req\"" 2>/dev/null; then
      ok "deps installed (core packages only, --no-deps)"
      rm -f "$core_req"
      warn "some voice-agent features may be unavailable — run \`$pip_cmd install -r $req\` to restore"
      return 0
    fi
  fi
  rm -f "$core_req"

  # Tier 3: last resort — install pip + setuptools and rely on venv stdlib
  warn "All dep install strategies failed — at minimum activate the venv and install dependencies manually"
  eval "$pip_cmd install --quiet pip setuptools" 2>/dev/null || true
}

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
      uv venv "$va/.venv" --python 3.13 || uv venv "$va/.venv" --python 3.12 || uv venv "$va/.venv"
      ok "created venv at $va/.venv via uv"
    else
      ok "venv exists; reusing"
    fi
    _install_voice_deps_tiered "$va"
  else
    if [ ! -d "$va/.venv" ]; then
      python3 -m venv "$va/.venv"
      ok "created venv at $va/.venv"
    else
      ok "venv exists; reusing"
    fi
    "$va/.venv/bin/pip" install --quiet --upgrade pip
    _install_voice_deps_tiered "$va" "no-uv"
  fi

  install_playwright_chromium "$va"
  install_systemd_units

  # Harden secret-bearing env files (owner-only)
  for f in "$VA_ENV" "$JARVIS_HOME/keys.env" "$JARVIS_HOME/local-api-token.env"; do
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
  # Probe for a system Chrome/Chromium first — if found, skip the 200 MB
  # Playwright download entirely. The browser tools can use the system
  # browser instead of the CDP fallback. Inspired by Hermes Agent's
  # find_system_browser().
  local browser_path=""
  for _b in google-chrome google-chrome-stable chromium chromium-browser chrome; do
    local _p; _p="$(command -v "$_b" 2>/dev/null)" || continue
    browser_path="$_p"
    break
  done
  if [ -z "$browser_path" ]; then
    for _p in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
              "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
      [ -x "$_p" ] && { browser_path="$_p"; break; }
    done
  fi
  if [ -n "$browser_path" ]; then
    ok "system browser found ($browser_path) — skipping Playwright Chromium download"
    # Write the path into the voice-agent .env so the browser tool
    # knows to use the system browser instead of Playwright's Chromium.
    if grep -q '^AGENT_BROWSER_EXECUTABLE_PATH=' "$VA_ENV" 2>/dev/null; then
      : # already set
    else
      printf '\n# Auto-detected system browser (install.sh)\nAGENT_BROWSER_EXECUTABLE_PATH=%s\n' "$browser_path" >> "$VA_ENV"
      ok "wrote AGENT_BROWSER_EXECUTABLE_PATH to $VA_ENV"
    fi
    return
  fi
  # Silent download — no prompt.
  "$va/.venv/bin/playwright" install chromium
  ok "Playwright Chromium installed"
}

# ── Config directory seeding ───────────────────────────────────────────
# Create the JARVIS_HOME config directory structure with default templates
# if they don't already exist (idempotent). Inspired by Hermes Agent's
# copy_config_templates().
copy_config_templates() {
  section "Seeding config directory"
  mkdir -p "$JARVIS_HOME/skills" "$JARVIS_HOME/plans"
  ok "config directory ready at $JARVIS_HOME"
}

# ── Service start (post-install) ──────────────────────────────────────
# Start the always-on services if the user has configured .env. Only
# starts services; doesn't fail if anything's missing (user may not have
# configured keys yet). Inspired by Hermes Agent's maybe_start_gateway().
maybe_start_services() {
  local _sctl="systemctl $([ "$SYSTEMD_SCOPE" = "system" ] && echo "" || echo "--user")"
  # Check if at least one LLM key is set before offering to start.
  local _has_key=0
  for _k in ANTHROPIC_API_KEY GROQ_API_KEY OPENAI_API_KEY; do
    [ -n "$(_env_get "$VA_ENV" "$_k")" ] || [ -n "$(_env_get "$INSTALL_DIR/.env" "$_k")" ] && _has_key=1
  done
  [ "$_has_key" = "0" ] && [ -n "$(_env_get "/etc/jarvis/.env" "ANTHROPIC_API_KEY")" ] && _has_key=1
  [ "$_has_key" = "0" ] && [ -n "$(_env_get "/etc/jarvis/voice-agent.env" "GROQ_API_KEY")" ] && _has_key=1

  if [ "$_has_key" = "1" ] && have systemctl; then
    $_sctl start livekit-server.service 2>/dev/null && ok "started livekit-server.service"
    sleep 1
    $_sctl start jarvis-voice-agent.service 2>/dev/null && ok "started jarvis-voice-agent.service"
  fi
}

install_systemd_units() {
  if ! have systemctl; then warn "no systemctl; skipping systemd unit install"; return; fi
  mkdir -p "$SYSTEMD_DIR"

  # Pre-create state + log dirs the units' ReadWritePaths= bind-mounts
  # require. Without these, the sandboxed units fail bring-up with
  # status=226/NAMESPACE (systemd refuses to bind-mount a non-existent
  # path even if the ExecStart script would create it). The units
  # have ExecStartPre fallbacks too — this is belt-and-suspenders.
  mkdir -p "$JARVIS_LOG_DIR"                # voice-agent + livekit-server log dest
  mkdir -p "$JARVIS_HOME/snapshots"          # hourly backup snapshots
  chmod 700 "$JARVIS_HOME/snapshots"         # contains telemetry detail

  local _sctl="systemctl $([ "$SYSTEMD_SCOPE" = "system" ] && echo "" || echo "--user")"

  if [ "$JARVIS_FHS" = "1" ]; then
    # ── System-scope units ──────────────────────────────────────────
    # Use the .system.service templates which have absolute paths and
    # User=jarvis. Simply copy with INSTALL_DIR substituted.
    for tmpl in jarvis-voice-agent.system.service jarvis-voice-client.system.service livekit-server.system.service; do
      local name="${tmpl%.system.service}.service"
      sed "s|/opt/jarvis|$INSTALL_DIR|g" "$INSTALL_DIR/setup/systemd/$tmpl" > "$SYSTEMD_DIR/$name"
      ok "installed system unit: $SYSTEMD_DIR/$name"
    done
    # Create system user for FHS services.
    if ! id -u jarvis >/dev/null 2>&1; then
      useradd --system --home-dir "$JARVIS_HOME" --shell /usr/sbin/nologin jarvis 2>/dev/null \
        && ok "created system user 'jarvis'" \
        || warn "could not create system user 'jarvis'"
    fi
  else
    # ── User-scope units ────────────────────────────────────────────
    # Use %h-based templates with sed path substitution.
    local sed_path_subs=(
      -e "s|%h/Documents/Projects/jarvis|$INSTALL_DIR|g"
      -e "s|/home/[^/]*/Documents/Projects/jarvis|$INSTALL_DIR|g"
      -e "s|/home/[^/]*/jarvis|$INSTALL_DIR|g"
    )
    local sed_unit="sed ${sed_path_subs[*]}"

    # Always-on services (voice-agent, voice-client, livekit-server).
    for src in jarvis-voice-agent.service jarvis-voice-client.service livekit-server.service; do
      $sed_unit "$INSTALL_DIR/setup/systemd/$src" > "$SYSTEMD_DIR/$src"
      ok "installed unit: $SYSTEMD_DIR/$src"
    done

    # Timer-driven maintenance units.
    for src in \
        jarvis-backup-local.service jarvis-backup-local.timer \
        jarvis-log-rotate.service jarvis-log-rotate.timer \
        jarvis-retention-prune.service jarvis-retention-prune.timer \
        jarvis-evolution-soak.service jarvis-evolution-soak.timer; do
      if [ -f "$INSTALL_DIR/setup/systemd/$src" ]; then
        $sed_unit "$INSTALL_DIR/setup/systemd/$src" > "$SYSTEMD_DIR/$src"
        ok "installed unit: $SYSTEMD_DIR/$src"
      fi
    done
  fi

  local _sctl="systemctl $([ "$SYSTEMD_SCOPE" = "system" ] && echo "" || echo "--user")"
  $_sctl daemon-reload

  # Enable always-on services (NOT started — user runs them after
  # configuring .env). Enable order matters: SFU first, then agent +
  # client.
  for unit in livekit-server.service jarvis-voice-agent.service jarvis-voice-client.service; do
    $_sctl enable "$unit" >/dev/null 2>&1 \
      && ok "enabled $unit (NOT started — configure .env first)" \
      || warn "could not enable $unit"
  done

  # Enable + start the maintenance timers — these are safe to start
  # immediately (they don't depend on .env or running provider APIs).
  # First fire happens per OnCalendar (hourly / 02:00 daily / 03:00
  # monthly-1st); Persistent=true catches up if laptop was off.
  for unit in jarvis-backup-local.timer jarvis-log-rotate.timer jarvis-retention-prune.timer jarvis-evolution-soak.timer; do
    if [ -f "$SYSTEMD_DIR/$unit" ]; then
      $_sctl enable --now "$unit" >/dev/null 2>&1 \
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
  local pkg_cmd; pkg_cmd="$(_pkg_mgr_cmd)"
  local bubblewrap_pkg="bubblewrap"

  if [ -n "$pkg_cmd" ] && [ "${JARVIS_DRY_RUN:-0}" != "1" ]; then
    sub "installing bubblewrap via $DISTRO package manager..."
    if sudo -n sh -c "$pkg_cmd $bubblewrap_pkg" >/dev/null 2>&1; then
      ok "bubblewrap installed"
    else
      warn "couldn't install bubblewrap (sudo? offline?); the bash tool will run un-sandboxed."
    fi
  else
    warn "bubblewrap NOT installed (no recognized package manager). The bash tool will run un-sandboxed."
  fi
}

# ── System packages (ripgrep + ffmpeg) ──────────────────────────────────
# Install ripgrep (CLI code-search) and ffmpeg (audio processing) if
# missing. Warn-only — the installer continues without them. Inspired by
# Hermes Agent's install_system_packages().
install_system_packages() {
  local need=()
  have rg    || need+=("ripgrep")
  have ffmpeg || need+=("ffmpeg")
  [ ${#need[@]} -eq 0 ] && { ok "ripgrep + ffmpeg already present"; return; }

  local pkg_cmd; pkg_cmd="$(_pkg_mgr_cmd)"
  if [ -z "$pkg_cmd" ]; then
    warn "no recognized package manager; install manually:"
    for pkg in "${need[@]}"; do warn "  sudo apt install $pkg  (or your distro's equivalent)"; done
    return
  fi

  section "Installing system packages (${need[*]})"
  sub "via $DISTRO package manager..."
  if sudo -n sh -c "$pkg_cmd ${need[*]}" >/dev/null 2>&1; then
    ok "${need[*]} installed"
  else
    warn "couldn't install ${need[*]} via $DISTRO pm (sudo? offline?)"
    # Fallback: cargo install ripgrep if missing
    if ! have rg && have cargo; then
      cargo install ripgrep >/dev/null 2>&1 && ok "ripgrep installed via cargo"
    fi
  fi
}

# ── Bridge auth token (pre-generated for first-run UX) ────────────────────
generate_bridge_token() {
  local token_file="$JARVIS_HOME/local-api-token.env"
  if [ -f "$token_file" ]; then
    ok "bridge token already exists at $token_file"
    return
  fi
  mkdir -p "$JARVIS_HOME"
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
  local url="https://github.com/livekit/livekit/releases/download/v${version}/livekit_${version}_linux_amd64.tar.gz"
  local sha_file="$INSTALL_DIR/setup/livekit-server.bin.sha256"

  section "Fetching livekit-server binary v${version} (~50 MB)"
  sub "URL: $url"

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT RETURN

  if ! curl -fL --progress-bar -o "$tmp_dir/livekit.tar.gz" "$url"; then
    die "Failed to download livekit-server tarball from $url"
  fi

  tar -xzf "$tmp_dir/livekit.tar.gz" -C "$tmp_dir"

  local extracted
  extracted=$(find "$tmp_dir" -maxdepth 2 -name 'livekit-server' -type f | head -1)
  if [ -z "$extracted" ]; then
    die "livekit-server binary not found in tarball — expected 'livekit-server' entry"
  fi

  # Verify checksum against pinned hash.
  if [ -f "$sha_file" ]; then
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
  local keys="$JARVIS_HOME/livekit-keys.yaml"
  local va_env="$VA_ENV"

  # If keys file already exists in proper YAML format (key: secret on
  # one line, no whitespace before the colon), leave it alone. Format
  # check: first non-comment line must match `^[A-Za-z0-9]+:[[:space:]]`.
  if [ -s "$keys" ] && awk '/^[^#]/ && /^[A-Za-z0-9]+:[[:space:]]/ {found=1; exit} END {exit !found}' "$keys"; then
    ok "LiveKit keys already at $keys"
    return
  fi
  mkdir -p "$JARVIS_HOME"

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
# ── Tauri system library pre-check ─────────────────────────────────────
# Probes for the shared libraries the Tauri build needs *before* the
# 10-minute cargo build, so we fail fast with a clear action message
# instead of a cryptic pkg-config error partway through.
_TAURI_DEPS=(libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libsoup-3.0-dev patchelf pkg-config)

check_desktop_prereqs() {
  if [ "${JARVIS_SKIP_DESKTOP:-0}" = "1" ]; then return; fi
  local missing=()
  for pkg in "${_TAURI_DEPS[@]}"; do
    if dpkg -l "$pkg" >/dev/null 2>&1; then
      : # present
    elif command -v pkg-config >/dev/null 2>&1 && pkg-config --exists "${pkg%-dev}" 2>/dev/null; then
      : # pkg-config alternative check
    else
      missing+=("$pkg")
    fi
  done
  if [ ${#missing[@]} -gt 0 ]; then
    err "Tauri build dependencies missing: ${missing[*]}"
    err "Install them and re-run:"
    err "  sudo apt install ${missing[*]}"
    err "(adjust package names for your distro if not using apt)"
    die "Desktop channel cannot build without the above system libraries."
  fi
  ok "Tauri build dependencies present"
}

install_desktop() {
  if [ "${JARVIS_SKIP_DESKTOP:-0}" = "1" ]; then warn "skipping Desktop (JARVIS_SKIP_DESKTOP=1)"; return; fi
  section "Installing Desktop (Tauri) — first build takes 5–10 min"

  check_desktop_prereqs

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
  # The actual JARVIS branding is the concentric-rings logo at
  # src-tauri/icons/jarvis-rings-128.png. Reuse it so the app-menu entry
  # matches what JARVIS looks like everywhere else.
  local icon_path="$INSTALL_DIR/src/desktop-tauri/src-tauri/icons/jarvis-rings-128.png"

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
  _resolve_paths
  local env_path="$([ "$JARVIS_FHS" = "1" ] && echo "/etc/jarvis/.env" || echo "$INSTALL_DIR/.env")"
  section "API key template"
  if [ -f "$env_path" ]; then
    ok ".env already exists at $env_path; not overwriting"
    return
  fi
  mkdir -p "$(dirname "$env_path")"
  cat > "$env_path" <<'EOF'
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
  chmod 600 "$env_path"
  ok "created $env_path (chmod 600 — fill in your real keys before starting the voice agent)"
}

# ── Final summary ────────────────────────────────────────────────────────
print_summary() {
  section "Done"
  local _sctl="systemctl $([ "$SYSTEMD_SCOPE" = "system" ] && echo "" || echo "--user")"
  local _env_path="$([ "$JARVIS_FHS" = "1" ] && echo "/etc/jarvis/.env" || echo "$INSTALL_DIR/.env")"
  local _journal_scope="$([ "$SYSTEMD_SCOPE" = "system" ] && echo "" || echo "--user")"
  cat <<EOF
  Install location:  $INSTALL_DIR
  CLI launcher:      $LOCAL_BIN/jarvis  (also $LOCAL_BIN/jarvis-desktop)
  Data directory:    $JARVIS_HOME

  Next steps:
    1. Edit $_env_path and fill in real API keys.
    2. Start the SFU + voice agent + voice client (in this order):
         $_sctl start livekit-server.service
         $_sctl start jarvis-voice-agent.service
         $_sctl start jarvis-voice-client.service
       Logs:
         journalctl $_journal_scope -u jarvis-voice-agent.service -f
         journalctl $_journal_scope -u jarvis-voice-client.service -f
    3. Try the CLI:
         jarvis
    4. Start the web app (optional):
         cd $INSTALL_DIR/src/web && bun dev
    5. Run the desktop app (Tauri):
         $INSTALL_DIR/src/desktop-tauri/src-tauri/target/release/jarvis-desktop
       (or click 'JARVIS' in your app launcher)

  Re-run this script anytime to re-install or update a channel.
  Skip channels with JARVIS_SKIP_{CLI,VOICE,DESKTOP,WEB}=1.

  Other commands:
    $LOCAL_BIN/jarvis-setup     re-run API key + persona config
    install.sh --setup           same, from the install script
    install.sh --ensure browser  install just Playwright Chromium
    install.sh --ensure voice-deps  reinstall voice-agent deps
EOF
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
  c_bold "JARVIS installer"
  detect_invocation
  detect_os
  detect_fhs
  check_network_prerequisites
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
  install_node               # managed Node.js tarball if system lacks one
  install_web
  install_voice_agent
  install_desktop
  install_bubblewrap     # bash-tool sandbox runtime (§P0-SEC-7)
  install_system_packages  # ripgrep + ffmpeg for CLI code-search + audio
  generate_bridge_token  # ~/.jarvis/local-api-token.env + web .env.local
  ensure_livekit_binary  # fetch livekit-server.bin at install time (not in git)
  setup_livekit_keys
  check_computer_use_deps  # optional probes for computer_use subagent
  install_audio_profile
  install_echo_cancel_aec
  configure
  copy_config_templates
  maybe_start_services
  print_summary
}

# ── --ensure mode: targeted dependency provisioning ──────────────────────
# Run `install.sh --ensure browser` to install just the browser dependency,
# or `install.sh --ensure node,voice-deps` for a multi-component install.
# Skips the full install pipeline; runs only the requested component(s).
# Inspired by Hermes Agent's `--ensure` flag.
ensure_mode() {
  local IFS=',' components
  components="${1:-${ENSURE_DEPS:-}}"
  [ -z "$components" ] && { warn "Usage: install.sh --ensure <component>[,component,...]"; return 0; }

  section "Ensure mode: ${components//,/, }"
  detect_invocation
  [ -d "$INSTALL_DIR/.git" ] || die "No checkout at '$INSTALL_DIR'. Run install.sh without --ensure first."

  for comp in $components; do
    case "$comp" in
      node)
        sub "ensuring Node.js..."
        if have node; then ok "node ($(node --version))"; else die "node not found — install it first"; fi
        if have npm;  then ok "npm  ($(npm --version))";  else die "npm not found — install Node.js first"; fi
        ;;
      browser)
        sub "ensuring browser deps (Playwright Chromium)..."
        install_playwright_chromium "$INSTALL_DIR/src/voice-agent"
        ;;
      voice-deps)
        sub "ensuring voice-agent deps..."
        local va="$INSTALL_DIR/src/voice-agent"
        [ -d "$va/.venv" ] || die "No venv at $va/.venv. Run full install.sh first."
        _install_voice_deps_tiered "$va"
        ;;
      cli)
        sub "ensuring CLI..."
        install_cli
        ;;
      desktop)
        sub "ensuring desktop..."
        install_desktop
        ;;
      web)
        sub "ensuring web..."
        install_web
        ;;
      *)
        warn "Unknown component: $comp (valid: node, browser, voice-deps, cli, desktop, web)"
        ;;
    esac
  done
  ok "ensure mode complete for: ${components//,/, }"
}

# ── --setup mode: re-runnable post-install configuration ────────────────
# Run `install.sh --setup` to re-run the configuration wizard (API keys,
# persona, .env) without reinstalling anything.
setup_mode() {
  section "Setup mode"
  detect_invocation
  [ -d "$INSTALL_DIR/.git" ] || die "No checkout at '$INSTALL_DIR'. Run install.sh first."
  configure
  print_summary
}

# ── --postinstall mode: pip-install user setup ─────────────────────────
# Designed for users who installed the voice-agent package via pip (e.g.,
# `pip install jarvis-voice-agent`). Runs only the post-clone steps that
# need user input or system deps: system packages, browser, systemd units,
# and configuration. Does NOT clone the repo, create a venv, or build the
# desktop app — those are assumed to have been handled by pip.
postinstall_mode() {
  section "Post-install setup"
  detect_invocation
  [ -d "$INSTALL_DIR/.git" ] || die "No checkout at '$INSTALL_DIR'. Run install.sh --postinstall from inside a cloned checkout."

  install_system_packages
  install_playwright_chromium "$INSTALL_DIR/src/voice-agent"
  install_systemd_units
  configure
  print_summary
}

# ── Entry point routing ─────────────────────────────────────────────────
# Called by install.sh after sourcing the lib. Dispatches to the right
# mode based on the first argument. Four modes: --ensure (targeted deps),
# --setup (config only), --postinstall (pip-user setup), or full install.
_entry_route() {
  case "${1:-}" in
    --ensure|--ensure=*)
      ensure_mode "${1#--ensure*=}"
      ;;
    --setup)
      setup_mode
      ;;
    --postinstall)
      postinstall_mode
      ;;
    *)
      main "$@"
      ;;
  esac
}

# Guard: run routing when executed directly (not sourced).
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  _entry_route "$@"
fi
