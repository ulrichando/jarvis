#!/usr/bin/env bats
# Unit tests for HUD helper scripts.

setup() {
  export HUD_BIN="$BATS_TEST_DIRNAME/../../hud/bin"
}

@test "fetch-status.sh outputs 'down' when MISTY_URL is unreachable" {
  run env MISTY_URL="http://127.0.0.1:1" bash "$HUD_BIN/fetch-status.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"health":"down"'* ]]
  [[ "$output" == *'"pending":[]'* ]]
}

@test "fetch-status.sh exits 0 (never errors out to eww)" {
  run env MISTY_URL="http://bad.invalid" bash "$HUD_BIN/fetch-status.sh"
  [ "$status" -eq 0 ]
}
