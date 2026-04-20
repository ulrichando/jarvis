#!/usr/bin/env bats
# Unit tests for host-side vm wrappers. Run: bats src/os/desktop/tests/scripts/vm.bats

setup() {
  export VM_DIR="$BATS_TEST_DIRNAME/../../scripts/vm"
}

@test "vm-config.env.example exists and sets VMX_PATH + VM_NAME" {
  local f="$VM_DIR/vm-config.env.example"
  [ -r "$f" ]
  grep -Eq '^VMX_PATH=' "$f"
  grep -Eq '^VM_NAME=' "$f"
}

@test "snapshot.sh requires a snapshot name argument" {
  run bash "$VM_DIR/snapshot.sh"
  [ "$status" -ne 0 ]
  [[ "$output" == *"usage: snapshot.sh"* ]] || [[ "$output" == *"vm-config.env"* ]]
}

@test "restore.sh requires a snapshot name argument" {
  run bash "$VM_DIR/restore.sh"
  [ "$status" -ne 0 ]
}

@test "all vm scripts use vmrun -T ws" {
  for script in snapshot.sh restore.sh list.sh; do
    run grep -F 'vmrun -T ws' "$VM_DIR/$script"
    [ "$status" -eq 0 ]
  done
}
