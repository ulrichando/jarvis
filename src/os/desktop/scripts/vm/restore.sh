#!/usr/bin/env bash
# restore.sh — revert the misty-base VM to a named snapshot. Usage: restore.sh <snapshot-name>

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
# shellcheck source=../install/lib.sh
source "../install/lib.sh"

config="$(dirname "$(readlink -f "$0")")/vm-config.env"
[[ -r "$config" ]] || die "vm-config.env not found. Copy vm-config.env.example → vm-config.env and edit."
# shellcheck disable=SC1090
source "$config"

require_bin vmrun
[[ -f "${VMX_PATH:?VMX_PATH not set}" ]] || die "VMX_PATH does not exist: $VMX_PATH"

snap="${1:?usage: restore.sh <snapshot-name>}"

# Power off if running, then revert, then start.
state=$(vmrun list | grep -F "$VMX_PATH" || true)
if [[ -n "$state" ]]; then
  log "VM is running; powering off before revert"
  vmrun -T ws stop "$VMX_PATH" hard || warn "stop returned non-zero"
fi

log "reverting ${VM_NAME:-VM} to snapshot '$snap'"
vmrun -T ws revertToSnapshot "$VMX_PATH" "$snap"

log "starting VM"
vmrun -T ws start "$VMX_PATH" nogui &
ok "VM reverted to '$snap' and starting in the background"
