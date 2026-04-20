#!/usr/bin/env bash
# 03-postinstall.sh — one-time post-install setup for pentest tools.
# Idempotent: running twice is a no-op.

set -euo pipefail
# shellcheck source=lib.sh
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/lib.sh"

require_arch

# --- Metasploit database ---
if command -v msfdb >/dev/null 2>&1; then
  log "initializing msfdb (Postgres for Metasploit)"
  # msfdb init is not idempotent-clean — it errors if already initialized.
  # We detect prior init by checking for the config file it creates.
  if [[ -f "$HOME/.msf4/database.yml" ]]; then
    ok "msfdb already initialized"
  else
    msfdb init || warn "msfdb init returned non-zero — check output, may already be set up"
  fi
else
  warn "msfdb not found; skipping Metasploit DB setup"
fi

# --- WPScan DB update ---
if command -v wpscan >/dev/null 2>&1; then
  log "updating wpscan vulnerability DB"
  wpscan --update || warn "wpscan --update failed (may need API token or network)"
else
  warn "wpscan not found; skipping"
fi

# --- searchsploit / exploit-db refresh ---
if command -v searchsploit >/dev/null 2>&1; then
  log "updating searchsploit / exploit-db"
  searchsploit -u || warn "searchsploit -u failed"
fi

# --- locate db ---
if command -v updatedb >/dev/null 2>&1; then
  log "refreshing locate db"
  sudo updatedb || warn "updatedb failed"
fi

# --- Wireshark: allow non-root capture ---
if command -v wireshark >/dev/null 2>&1; then
  if getent group wireshark >/dev/null; then
    if id -nG "$(id -un)" | grep -qw wireshark; then
      ok "already in wireshark group"
    else
      log "adding $(id -un) to wireshark group (non-root capture)"
      sudo usermod -aG wireshark "$(id -un)" || warn "usermod failed — add yourself to the wireshark group manually"
      warn "log out and back in for group change to take effect"
    fi
  fi
fi

ok "post-install tasks complete"
