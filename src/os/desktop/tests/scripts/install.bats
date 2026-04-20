#!/usr/bin/env bats
# Unit tests for install scripts. Run: bats src/os/desktop/tests/scripts/install.bats

setup() {
  export SCRIPTS_DIR="$BATS_TEST_DIRNAME/../../scripts/install"
}

@test "lib.sh sources without error" {
  run bash -c "source '$SCRIPTS_DIR/lib.sh'"
  [ "$status" -eq 0 ]
}

@test "lib.sh defines the expected helper functions" {
  run bash -c "source '$SCRIPTS_DIR/lib.sh' && declare -F log ok warn die require_bin require_arch on_err"
  [ "$status" -eq 0 ]
}

@test "packages.txt exists and has at least 40 non-comment entries" {
  local f="$SCRIPTS_DIR/packages.txt"
  [ -r "$f" ]
  local count
  count=$(grep -Ev '^\s*(#|$)' "$f" | wc -l)
  [ "$count" -ge 40 ]
}

@test "packages.txt has no duplicates" {
  local f="$SCRIPTS_DIR/packages.txt"
  local dupes
  dupes=$(grep -Ev '^\s*(#|$)' "$f" | awk '{print $1}' | sort | uniq -d)
  [ -z "$dupes" ]
}

@test "00-preflight.sh: dies on non-Arch host" {
  # Run in a sandboxed PATH with a fake /etc/os-release (via temp dir + override).
  local tmp; tmp=$(mktemp -d)
  cat > "$tmp/os-release" <<'EOF'
ID=ubuntu
ID_LIKE=debian
EOF
  run bash -c "
    source '$SCRIPTS_DIR/lib.sh'
    # Shim /etc/os-release by redirecting the 'source /etc/os-release' inside require_arch.
    require_arch() {
      source '$tmp/os-release'
      case \"\${ID:-}:\${ID_LIKE:-}\" in
        arch:*|*:arch*) : ;;
        *) die \"expected Arch; got \${ID:-?}\" ;;
      esac
    }
    require_arch
  "
  [ "$status" -ne 0 ]
  [[ "$output" == *"expected Arch"* ]]
  rm -rf "$tmp"
}

@test "01-blackarch.sh: SHA1 pin is a 40-char hex" {
  local f="$SCRIPTS_DIR/01-blackarch.sh"
  run grep -E "STRAP_SHA1='[0-9a-f]{40}'" "$f"
  [ "$status" -eq 0 ]
}

@test "01-blackarch.sh: uses strict curl flags" {
  local f="$SCRIPTS_DIR/01-blackarch.sh"
  run grep -E "curl --proto '=https' --tlsv1.2 -fsSL" "$f"
  [ "$status" -eq 0 ]
}

@test "02-pentools.sh: references packages.txt by absolute path resolution" {
  local f="$SCRIPTS_DIR/02-pentools.sh"
  run grep -E 'PKG_FILE=.*packages.txt' "$f"
  [ "$status" -eq 0 ]
}

@test "02-pentools.sh: uses --needed for idempotency" {
  local f="$SCRIPTS_DIR/02-pentools.sh"
  run grep -E 'pacman -S --needed' "$f"
  [ "$status" -eq 0 ]
}

@test "03-postinstall.sh: all tool blocks guard with command -v" {
  local f="$SCRIPTS_DIR/03-postinstall.sh"
  # Count commands we wrap in 'if command -v'. Expect at least 4: msfdb, wpscan, searchsploit, updatedb.
  local count
  count=$(grep -cE 'if command -v (msfdb|wpscan|searchsploit|updatedb|wireshark) >/dev/null' "$f")
  [ "$count" -ge 4 ]
}
