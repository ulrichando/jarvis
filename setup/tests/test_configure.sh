#!/usr/bin/env bash
# Tests for install.sh's first-run configure() helpers.
# Sources install.sh (which must NOT run main() when sourced) and exercises
# the functions against throwaway temp dirs. Never touches the live install.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
FAILS=0
check() { # <name> <test-expression-string>
  if eval "$2"; then echo "ok - $1"; else echo "NOT ok - $1"; FAILS=$((FAILS + 1)); fi
}

# SAFETY GATE: never source install.sh unless it guards its main() call.
# An unguarded source would execute the WHOLE installer (clone, pip/cargo
# builds, systemd enable, ...) — catastrophic on a live box. If the guard is
# missing, fail loudly and exit WITHOUT sourcing.
if ! grep -qF 'if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then' "$REPO/install.sh"; then
  echo "NOT ok - install.sh must guard main() before it can be sourced for tests"
  echo "1 failures"; exit 1
fi

# Guard present → sourcing is safe. Confirm it emits no installer output...
SRC_OUT="$(source "$REPO/install.sh" 2>&1; echo SENTINEL)"
check "sourcing install.sh runs no installer" '[ "$SRC_OUT" = "SENTINEL" ]'

# ...then load the functions into this shell for the remaining tests.
source "$REPO/install.sh"
set +e +u  # install.sh's `set -euo pipefail` leaks in; relax for asserts.

echo "---"; echo "$FAILS failures"; [ "$FAILS" -eq 0 ]
