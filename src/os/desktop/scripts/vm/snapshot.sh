#!/usr/bin/env bash
# snapshot.sh — snapshot the misty-base VM. Usage: snapshot.sh <snapshot-name>

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

snap="${1:?usage: snapshot.sh <snapshot-name>}"

log "snapshotting ${VM_NAME:-VM} → '$snap'"
vmrun -T ws snapshot "$VMX_PATH" "$snap"
ok "snapshot created: $snap"
