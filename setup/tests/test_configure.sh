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

# ── _env_get / _env_upsert ───────────────────────────────────────────
T1="$(mktemp -d)"; EF="$T1/.env"
_env_upsert "$EF" FOO bar
check "_env_upsert creates + sets" '[ "$(grep -c "^FOO=bar$" "$EF")" = 1 ]'
_env_upsert "$EF" FOO baz
check "_env_upsert replaces (no dup)" '[ "$(grep -c "^FOO=" "$EF")" = 1 ] && [ "$(_env_get "$EF" FOO)" = baz ]'
_env_upsert "$EF" OTHER keep
check "_env_upsert preserves other keys" '[ "$(_env_get "$EF" FOO)" = baz ] && [ "$(_env_get "$EF" OTHER)" = keep ]'
check "_env_upsert chmod 600" '[ "$(stat -c %a "$EF")" = 600 ]'
check "_env_get missing key is empty" '[ -z "$(_env_get "$EF" NOPE)" ]'

# ── _interactive + prompt helpers ────────────────────────────────────
check "_interactive false when NONINTERACTIVE=1" '! ( JARVIS_NONINTERACTIVE=1 _interactive )'
check "_interactive false when DRY_RUN=1"        '! ( JARVIS_DRY_RUN=1 _interactive )'
check "_interactive false when SKIP_SETUP=1"     '! ( JARVIS_SKIP_SETUP=1 _interactive )'
TTYF="$T1/answers"; printf 'x\n' > "$TTYF"
check "_interactive true with readable _JARVIS_TTY" '( _JARVIS_TTY="$TTYF" JARVIS_NONINTERACTIVE=0 JARVIS_DRY_RUN=0 JARVIS_SKIP_SETUP=0 _interactive )'

printf 'typed\n' > "$TTYF"
check "_ask returns typed value" '[ "$(_JARVIS_TTY="$TTYF" _ask "p: " def)" = typed ]'
printf '\n' > "$TTYF"
check "_ask returns default on blank" '[ "$(_JARVIS_TTY="$TTYF" _ask "p: " def)" = def ]'
printf 'secret123\n' > "$TTYF"
check "_ask_secret reads hidden value" '[ "$(_JARVIS_TTY="$TTYF" _ask_secret "p: ")" = secret123 ]'
printf 'y\n' > "$TTYF"
check "_confirm yes" '( _JARVIS_TTY="$TTYF" _confirm "p? " N )'
printf 'n\n' > "$TTYF"
check "_confirm no" '! ( _JARVIS_TTY="$TTYF" _confirm "p? " Y )'
printf '\n' > "$TTYF"
check "_confirm blank honors default Y" '( _JARVIS_TTY="$TTYF" _confirm "p? " Y )'

echo "---"; echo "$FAILS failures"; [ "$FAILS" -eq 0 ]
