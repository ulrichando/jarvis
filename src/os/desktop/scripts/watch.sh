#!/usr/bin/env bash
# Stream jarvis's live conversation (you + jarvis + tool calls) from the VM.
# Usage: ./scripts/watch.sh [vm-ip]
# Requires: ssh key in ~/.ssh/id_ed25519 and user 'ulrich' on the VM.

set -euo pipefail

VM="${1:-192.168.77.130}"
# -o LogLevel=QUIET silences 'Connection to ... closed' noise.
# -T disables pseudo-tty allocation on the server; we use stdbuf for
# line-buffering instead, which is cleaner than TTY (no escape-code smear).
SSH_OPTS="-i ${HOME}/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o LogLevel=QUIET -T"

# Colors (use tput so they work across terminals)
if [[ -t 1 ]]; then
  GREEN=$(tput setaf 2); CYAN=$(tput setaf 6); AMBER=$(tput setaf 3)
  RED=$(tput setaf 1); DIM=$(tput dim); BOLD=$(tput bold); RESET=$(tput sgr0)
else
  GREEN=""; CYAN=""; AMBER=""; RED=""; DIM=""; BOLD=""; RESET=""
fi

echo "${BOLD}${CYAN}▸ Streaming jarvis conversation from ${VM}${RESET}"
echo "${DIM}  (ctrl-c to stop)${RESET}"
echo

# Follow misty-talk + misty-core logs together. Pretty-print known line
# prefixes; pass everything else through dimmed.
# stdbuf -oL forces journalctl to line-buffer its stdout — otherwise the SSH
# pipe block-buffers and lines arrive in batches seconds apart.
ssh $SSH_OPTS ulrich@"$VM" '
  exec stdbuf -oL journalctl --user -u misty-talk -u misty-core -f -n 0 --output=cat
' | while IFS= read -r line; do
  case "$line" in
    *"[you]"*)
      msg="${line#*\[you\]}"
      echo "${BOLD}${GREEN}▸ you${RESET}${GREEN}:${msg}${RESET}"
      ;;
    *"[misty]"*)
      msg="${line#*\[misty\]}"
      echo "${BOLD}${CYAN}▸ jarvis${RESET}${CYAN}:${msg}${RESET}"
      ;;
    *"utterance ended"*|*"voice detected"*)
      echo "${DIM}  · ${line##*] }${RESET}"
      ;;
    *"ignoring"*|*"turn failed"*)
      echo "${AMBER}  ⚠ ${line##*] }${RESET}"
      ;;
    *"/api/think error"*|*"groq chat.completions failed"*)
      echo "${RED}  ✗ ${line##*] }${RESET}"
      ;;
    *"[misty-core]"*)
      # Startup/shutdown banners
      echo "${DIM}  ◆ ${line##*bun*: }${RESET}"
      ;;
    *)
      # Drop noisy systemd lifecycle lines
      case "$line" in
        *"systemd["*|*"Started "*|*"Stopped "*|*"Stopping "*|*"Consumed "*) continue ;;
      esac
      echo "${DIM}    ${line}${RESET}"
      ;;
  esac
done
