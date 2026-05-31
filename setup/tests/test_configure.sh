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

# ── configure_api_keys ───────────────────────────────────────────────
T2="$(mktemp -d)"; mkdir -p "$T2/src/voice-agent"
# answers: anthropic, groq, deepgram, then "n" to extra providers
printf 'sk-ant-1\nsk-groq-2\ndg-3\nn\n' > "$T2/ans"
( export INSTALL_DIR="$T2"; _JARVIS_TTY="$T2/ans" configure_api_keys ) >/dev/null 2>&1
check "anthropic -> root .env"          '[ "$(_env_get "$T2/.env" ANTHROPIC_API_KEY)" = sk-ant-1 ]'
check "groq -> root .env"               '[ "$(_env_get "$T2/.env" GROQ_API_KEY)" = sk-groq-2 ]'
check "deepgram -> voice-agent/.env"    '[ "$(_env_get "$T2/src/voice-agent/.env" DEEPGRAM_API_KEY)" = dg-3 ]'
check "root .env chmod 600"             '[ "$(stat -c %a "$T2/.env")" = 600 ]'
check "untouched provider stays unset"  '[ -z "$(_env_get "$T2/.env" OPENAI_API_KEY)" ]'

# blank answers skip everything (no .env written for keys)
T2b="$(mktemp -d)"; mkdir -p "$T2b/src/voice-agent"
printf '\n\n\nn\n' > "$T2b/ans"
( export INSTALL_DIR="$T2b"; _JARVIS_TTY="$T2b/ans" configure_api_keys ) >/dev/null 2>&1
check "blank input sets no anthropic key" '[ -z "$(_env_get "$T2b/.env" ANTHROPIC_API_KEY)" ]'

# ── configure_soul (Option A: copy base soul -> ~/.jarvis/SOUL.md) ────
T3="$(mktemp -d)"
mkdir -p "$T3/src/voice-agent/prompts" "$T3/home"
printf 'You are JARVIS. JARVIS helps you.\n' > "$T3/src/voice-agent/prompts/soul.md"
# answers: personalize? Y ; name -> Aria ; open editor? n
printf 'Y\nAria\nn\n' > "$T3/ans"
( export INSTALL_DIR="$T3" HOME="$T3/home" EDITOR=true; _JARVIS_TTY="$T3/ans" configure_soul ) >/dev/null 2>&1
check "SOUL.md created"        '[ -f "$T3/home/.jarvis/SOUL.md" ]'
check "name applied to soul"   'grep -q "You are Aria. Aria helps you." "$T3/home/.jarvis/SOUL.md"'
check "SOUL.md chmod 600"      '[ "$(stat -c %a "$T3/home/.jarvis/SOUL.md")" = 600 ]'

# decline -> no SOUL.md written
T3b="$(mktemp -d)"; mkdir -p "$T3b/src/voice-agent/prompts" "$T3b/home"
printf 'You are JARVIS.\n' > "$T3b/src/voice-agent/prompts/soul.md"
printf 'n\n' > "$T3b/ans"
( export INSTALL_DIR="$T3b" HOME="$T3b/home" EDITOR=true; _JARVIS_TTY="$T3b/ans" configure_soul ) >/dev/null 2>&1
check "decline leaves no SOUL.md" '[ ! -f "$T3b/home/.jarvis/SOUL.md" ]'

# keep default name -> verbatim copy
T3c="$(mktemp -d)"; mkdir -p "$T3c/src/voice-agent/prompts" "$T3c/home"
printf 'You are JARVIS, the assistant.\n' > "$T3c/src/voice-agent/prompts/soul.md"
printf 'Y\n\nn\n' > "$T3c/ans"   # personalize Y, name blank (keep JARVIS), editor n
( export INSTALL_DIR="$T3c" HOME="$T3c/home" EDITOR=true; _JARVIS_TTY="$T3c/ans" configure_soul ) >/dev/null 2>&1
check "blank name = verbatim copy" 'diff -q "$T3c/src/voice-agent/prompts/soul.md" "$T3c/home/.jarvis/SOUL.md" >/dev/null'

echo "---"; echo "$FAILS failures"; [ "$FAILS" -eq 0 ]
