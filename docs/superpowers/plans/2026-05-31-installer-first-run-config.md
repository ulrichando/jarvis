# Installer First-Run Config (API keys + persona) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive `configure()` stage to the all-in-one `install.sh` that collects API keys per provider and personalizes the assistant's soul (`~/.jarvis/SOUL.md`), degrading safely to a non-interactive template when no terminal is reachable.

**Architecture:** New shell functions in `install.sh`, called from `main()` after the build stages. Prompts read/write through a resolvable terminal path (`/dev/tty` in real use, a fixture file in tests) so `curl | bash` still prompts and the functions stay unit-testable. A small bash test harness sources `install.sh` (guarded so `main` doesn't run when sourced) and exercises each function against temp dirs.

**Tech Stack:** Bash (`set -euo pipefail`), GNU coreutils (`stat`, `sed`, `grep`, `mktemp`), `shellcheck`/`bash -n` for static checks. No new runtime deps. `prompt_builder.py` is NOT touched (the 30,331-char `soul.md` fits the 40,000 `MAX_SOUL_CHARS` override cap).

**Spec:** `docs/superpowers/specs/2026-05-31-installer-first-run-config-design.md`

---

## File Structure

- **Modify `install.sh`:**
  - Guard the `main "$@"` call so the file is sourceable for tests.
  - Add helpers: `_tty_path`, `_interactive`, `_ask`, `_ask_secret`, `_confirm`, `_env_get`, `_env_upsert`, `_maybe_set_key`.
  - Add `configure_api_keys`, `configure_soul`, `configure`.
  - Replace the `setup_env_template` call in `main()` with `configure` (keep `setup_env_template` — `configure` calls it).
- **Create `setup/tests/test_configure.sh`:** runnable bash test harness (sources `install.sh`, asserts against temp dirs, prints TAP-ish lines, exits non-zero on failure).
- **Untouched:** `src/voice-agent/pipeline/prompt_builder.py`, systemd, git `prompts/soul.md`, `src/cli/`, web/desktop.

All helpers are pure-ish (operate on args + `$INSTALL_DIR`/`$HOME`), so tests point those at temp dirs.

---

### Task 0: Make `install.sh` sourceable (guard `main`)

**Files:**
- Modify: `install.sh` (last line, currently `main "$@"`)
- Create: `setup/tests/test_configure.sh`

- [ ] **Step 1: Write the failing test**

Create `setup/tests/test_configure.sh`:

```bash
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
```

> **Safety note:** this harness refuses to `source` `install.sh` until the
> `main()` guard (Step 3) exists. Running it beforehand fails the grep gate and
> exits **without** sourcing, so the installer is never executed by the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `bash setup/tests/test_configure.sh`
Expected: FAIL — the guard isn't present yet, so the safety gate prints
`NOT ok - install.sh must guard main() before it can be sourced for tests` and
`1 failures`, exit 1. The harness does **not** source `install.sh`, so the
installer never runs.

- [ ] **Step 3: Guard the main call**

In `install.sh`, replace the final line:

```bash
main "$@"
```

with:

```bash
# Only run the installer when executed directly (bash install.sh / curl|bash),
# not when sourced by the test harness. curl|bash: BASH_SOURCE[0] is unset →
# falls to $0 ("bash") which equals $0 → runs. Sourced: BASH_SOURCE[0] is the
# install.sh path while $0 is the caller → not equal → does not run.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  main "$@"
fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash setup/tests/test_configure.sh`
Expected: PASS — `ok - sourcing install.sh runs nothing`, `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add install.sh setup/tests/test_configure.sh
git commit -m "test(install): make install.sh sourceable + add configure test harness"
```

---

### Task 1: `_env_get` / `_env_upsert` — idempotent .env writer

**Files:**
- Modify: `install.sh` (add helpers after the existing `have()` definition, ~line 53)
- Modify: `setup/tests/test_configure.sh`

- [ ] **Step 1: Write the failing test**

In `setup/tests/test_configure.sh`, insert before the final `echo "---"` block:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash setup/tests/test_configure.sh`
Expected: FAIL — `_env_upsert: command not found` / `NOT ok - _env_upsert ...`.

- [ ] **Step 3: Write the helpers**

In `install.sh`, after `have() { command -v "$1" >/dev/null 2>&1; }`:

```bash
# ── .env read/write helpers ──────────────────────────────────────────
# _env_get <file> <VAR> — print the current value of VAR (empty if unset/missing).
_env_get() {
  local file="$1" var="$2"
  [ -f "$file" ] || return 0
  grep -E "^${var}=" "$file" 2>/dev/null | tail -1 | sed "s/^${var}=//"
}

# _env_upsert <file> <VAR> <value> — set VAR=value idempotently. Replaces an
# existing `^VAR=` line (any value) or appends; preserves all other lines;
# creates the file + parent dir if missing; chmod 600.
_env_upsert() {
  local file="$1" var="$2" value="$3" tmp
  mkdir -p "$(dirname "$file")"
  [ -f "$file" ] || : > "$file"
  tmp="$(mktemp)"
  grep -v -E "^${var}=" "$file" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$var" "$value" >> "$tmp"
  mv "$tmp" "$file"
  chmod 600 "$file"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash setup/tests/test_configure.sh`
Expected: PASS — all `_env_*` checks `ok`, `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add install.sh setup/tests/test_configure.sh
git commit -m "feat(install): _env_get/_env_upsert idempotent .env helpers"
```

---

### Task 2: `_interactive` + prompt helpers

**Files:**
- Modify: `install.sh` (add after the `.env` helpers from Task 1)
- Modify: `setup/tests/test_configure.sh`

- [ ] **Step 1: Write the failing test**

In `setup/tests/test_configure.sh`, before the final block:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash setup/tests/test_configure.sh`
Expected: FAIL — `_interactive: command not found` / related `NOT ok` lines.

- [ ] **Step 3: Write the helpers**

In `install.sh`, after the `.env` helpers:

```bash
# ── Interactivity + prompts ──────────────────────────────────────────
# Tests inject a fixture file via _JARVIS_TTY; real runs use /dev/tty so even
# `curl | bash` (whose stdin is the script) can prompt the user.
_tty_path() { printf '%s' "${_JARVIS_TTY:-/dev/tty}"; }

# True iff a terminal is reachable and the user hasn't opted out.
_interactive() {
  [ "${JARVIS_NONINTERACTIVE:-0}" = "1" ] && return 1
  [ "${JARVIS_DRY_RUN:-0}" = "1" ]        && return 1
  [ "${JARVIS_SKIP_SETUP:-0}" = "1" ]     && return 1
  [ -t 0 ] && return 0
  [ -r "$(_tty_path)" ] && return 0
  return 1
}

# _ask <prompt> <default> — echo the answer, or <default> if blank.
_ask() {
  local prompt="$1" default="$2" ans tty; tty="$(_tty_path)"
  printf '%s' "$prompt" > "$tty" 2>/dev/null || printf '%s' "$prompt" >&2
  IFS= read -r ans < "$tty" 2>/dev/null || ans=""
  printf '%s' "${ans:-$default}"
}

# _ask_secret <prompt> — echo the typed secret without terminal echo.
_ask_secret() {
  local prompt="$1" ans tty; tty="$(_tty_path)"
  printf '%s' "$prompt" > "$tty" 2>/dev/null || printf '%s' "$prompt" >&2
  IFS= read -rs ans < "$tty" 2>/dev/null || ans=""
  printf '\n' > "$tty" 2>/dev/null || true
  printf '%s' "$ans"
}

# _confirm <prompt> <default:Y|N> — return 0 for yes, 1 for no.
_confirm() {
  local prompt="$1" default="${2:-N}" ans
  ans="$(_ask "$prompt" "$default")"
  case "$ans" in [Yy]|[Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash setup/tests/test_configure.sh`
Expected: PASS — all `_interactive`/`_ask`/`_confirm` checks `ok`, `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add install.sh setup/tests/test_configure.sh
git commit -m "feat(install): _interactive + tty-backed prompt helpers"
```

---

### Task 3: `configure_api_keys`

**Files:**
- Modify: `install.sh` (add after the prompt helpers)
- Modify: `setup/tests/test_configure.sh`

- [ ] **Step 1: Write the failing test**

In `setup/tests/test_configure.sh`, before the final block:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash setup/tests/test_configure.sh`
Expected: FAIL — `configure_api_keys: command not found`.

- [ ] **Step 3: Write `configure_api_keys` + `_maybe_set_key`**

In `install.sh`, after the prompt helpers:

```bash
# _maybe_set_key <label> <VAR> <file> — prompt for one key, write if non-empty,
# guarding against silently replacing an existing value.
_maybe_set_key() {
  local label="$1" var="$2" file="$3" existing val
  existing="$(_env_get "$file" "$var")"
  if [ -n "$existing" ]; then
    _confirm "  $label ($var) already set — replace? [y/N] " N || return 0
  fi
  val="$(_ask_secret "  $label key ($var) [blank=skip]: ")"
  if [ -n "$val" ]; then
    _env_upsert "$file" "$var" "$val"
    ok "$var saved"
  fi
}

configure_api_keys() {
  local root_env="$INSTALL_DIR/.env"
  local va_env="$INSTALL_DIR/src/voice-agent/.env"
  sub "API keys — press Enter to skip any provider."

  _maybe_set_key "Anthropic"      ANTHROPIC_API_KEY "$root_env"
  _maybe_set_key "Groq"           GROQ_API_KEY      "$root_env"
  _maybe_set_key "Deepgram (STT)" DEEPGRAM_API_KEY  "$va_env"

  if _confirm "  Configure more providers (OpenAI/DeepSeek/Google/Kimi)? [y/N] " N; then
    _maybe_set_key "OpenAI"   OPENAI_API_KEY   "$root_env"
    _maybe_set_key "DeepSeek" DEEPSEEK_API_KEY "$root_env"
    _maybe_set_key "Google"   GOOGLE_API_KEY   "$root_env"
    _maybe_set_key "Kimi"     KIMI_API_KEY     "$root_env"
  fi

  local v has_llm=""
  for v in ANTHROPIC_API_KEY GROQ_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY GOOGLE_API_KEY KIMI_API_KEY; do
    [ -n "$(_env_get "$root_env" "$v")" ] && has_llm=1
  done
  [ -z "$has_llm" ] && warn "No LLM key set — add one to $root_env before starting the voice agent."
  return 0
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash setup/tests/test_configure.sh`
Expected: PASS — all `configure_api_keys` checks `ok`, `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add install.sh setup/tests/test_configure.sh
git commit -m "feat(install): interactive per-provider API-key capture (configure_api_keys)"
```

---

### Task 4: `configure_soul` (Option A — copy → edit)

**Files:**
- Modify: `install.sh` (add after `configure_api_keys`)
- Modify: `setup/tests/test_configure.sh`

- [ ] **Step 1: Write the failing test**

In `setup/tests/test_configure.sh`, before the final block:

```bash
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash setup/tests/test_configure.sh`
Expected: FAIL — `configure_soul: command not found`.

- [ ] **Step 3: Write `configure_soul`**

In `install.sh`, after `configure_api_keys`:

```bash
configure_soul() {
  local soul_src="$INSTALL_DIR/src/voice-agent/prompts/soul.md"
  local soul_dst="$HOME/.jarvis/SOUL.md"
  if [ ! -f "$soul_src" ]; then
    warn "base soul not found at $soul_src — skipping persona setup"; return 0
  fi

  if [ -f "$soul_dst" ]; then
    _confirm "  ~/.jarvis/SOUL.md exists — overwrite to re-personalize? [y/N] " N \
      || { ok "keeping existing $soul_dst"; return 0; }
  else
    _confirm "  Personalize the assistant's persona now? [Y/n] " Y \
      || { sub "skipping persona (JARVIS uses the built-in soul)"; return 0; }
  fi

  mkdir -p "$HOME/.jarvis"
  cp "$soul_src" "$soul_dst"
  chmod 600 "$soul_dst"

  # Optional rename. Only [A-Za-z0-9 _-] accepted (no sed metachar injection).
  local name; name="$(_ask "  Assistant name [JARVIS]: " "JARVIS")"
  if [ -n "$name" ] && [ "$name" != "JARVIS" ]; then
    if printf '%s' "$name" | grep -qE '^[A-Za-z0-9 _-]+$'; then
      sed -i "s/\\bJARVIS\\b/${name}/g" "$soul_dst"
      ok "set assistant name to '$name'"
    else
      warn "name has unsupported characters — keeping 'JARVIS'"
    fi
  fi
  ok "wrote $soul_dst (chmod 600)"

  # Offer the editor for hand tweaks. EDITOR=true (tests) is a no-op.
  local editor="${EDITOR:-}"
  [ -z "$editor" ] && { have nano && editor=nano || editor=vi; }
  if _confirm "  Open $soul_dst in $editor to fine-tune? [Y/n] " Y; then
    "$editor" "$soul_dst" < "$(_tty_path)" > "$(_tty_path)" 2>&1 \
      || warn "editor exited non-zero; $soul_dst left as written"
  fi
  return 0
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash setup/tests/test_configure.sh`
Expected: PASS — all `configure_soul` checks `ok`, `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add install.sh setup/tests/test_configure.sh
git commit -m "feat(install): persona setup — copy soul to ~/.jarvis/SOUL.md + optional rename (configure_soul)"
```

---

### Task 5: `configure()` orchestration + wire into `main()`

**Files:**
- Modify: `install.sh` (add `configure`; change the `main()` call site)
- Modify: `setup/tests/test_configure.sh`

- [ ] **Step 1: Write the failing test**

In `setup/tests/test_configure.sh`, before the final block:

```bash
# ── configure() orchestration ────────────────────────────────────────
# Non-interactive: writes the .env template, runs no prompts, does not hang.
T5="$(mktemp -d)"; mkdir -p "$T5/src/voice-agent/prompts"
printf 'You are JARVIS.\n' > "$T5/src/voice-agent/prompts/soul.md"
( export INSTALL_DIR="$T5" HOME="$T5/home" JARVIS_NONINTERACTIVE=1; configure ) >/dev/null 2>&1
check "non-interactive writes .env template" 'grep -q "^ANTHROPIC_API_KEY=" "$T5/.env"'
check "non-interactive makes no SOUL.md"     '[ ! -f "$T5/home/.jarvis/SOUL.md" ]'

# SKIP_SETUP bypasses prompts but still leaves a usable template.
T5b="$(mktemp -d)"; mkdir -p "$T5b/src/voice-agent/prompts"
printf 'You are JARVIS.\n' > "$T5b/src/voice-agent/prompts/soul.md"
( export INSTALL_DIR="$T5b" HOME="$T5b/home" JARVIS_SKIP_SETUP=1; configure ) >/dev/null 2>&1
check "skip-setup still writes template" '[ -f "$T5b/.env" ]'

# Interactive end-to-end: keys + soul via fixtures.
T5c="$(mktemp -d)"; mkdir -p "$T5c/src/voice-agent/prompts"
printf 'You are JARVIS.\n' > "$T5c/src/voice-agent/prompts/soul.md"
# anthropic, groq, deepgram, more? n, personalize? Y, name blank, editor n
printf 'sk-a\nsk-g\ndg\nn\nY\n\nn\n' > "$T5c/ans"
( export INSTALL_DIR="$T5c" HOME="$T5c/home" EDITOR=true \
    JARVIS_NONINTERACTIVE=0 JARVIS_DRY_RUN=0 JARVIS_SKIP_SETUP=0; \
  _JARVIS_TTY="$T5c/ans" configure ) >/dev/null 2>&1
check "interactive writes a key"  '[ "$(_env_get "$T5c/.env" ANTHROPIC_API_KEY)" = sk-a ]'
check "interactive writes SOUL.md" '[ -f "$T5c/home/.jarvis/SOUL.md" ]'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash setup/tests/test_configure.sh`
Expected: FAIL — `configure: command not found`.

- [ ] **Step 3: Write `configure` and rewire `main()`**

In `install.sh`, after `configure_soul`:

```bash
configure() {
  if [ "${JARVIS_SKIP_SETUP:-0}" = "1" ]; then
    warn "skipping first-run setup (JARVIS_SKIP_SETUP=1)"
    setup_env_template
    return 0
  fi
  section "First-run configuration"
  if _interactive; then
    setup_env_template      # ensure .env exists (template + optional-knob comments)
    configure_api_keys      # upsert real keys into .env + voice-agent/.env
    configure_soul          # optional persona override at ~/.jarvis/SOUL.md
  else
    sub "non-interactive shell — writing the key template; edit it to add keys."
    setup_env_template
    sub "Personalize later: copy src/voice-agent/prompts/soul.md to ~/.jarvis/SOUL.md and edit."
  fi
  return 0
}
```

Then in `main()`, replace the line:

```bash
  setup_env_template
```

with:

```bash
  configure
```

(Leave `setup_env_template`'s definition in place — `configure` calls it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `bash setup/tests/test_configure.sh`
Expected: PASS — all `configure` checks `ok`, `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add install.sh setup/tests/test_configure.sh
git commit -m "feat(install): wire configure() into main (interactive setup + non-interactive fallback)"
```

---

### Task 6: Static checks + full-suite gate

**Files:**
- Modify: `install.sh` (only if shellcheck flags something)

- [ ] **Step 1: Syntax check**

Run: `bash -n install.sh`
Expected: no output, exit 0.

- [ ] **Step 2: Shellcheck (errors only)**

Run: `shellcheck -S error install.sh setup/tests/test_configure.sh`
Expected: no error-level findings. (If `shellcheck` is absent: `sudo apt-get install -y shellcheck`. Fix any error-level issues — e.g. quote expansions — and re-run.)

- [ ] **Step 3: Dry-run still works (no regression to the existing path)**

Run: `JARVIS_INSTALL_DIR="$PWD" JARVIS_DRY_RUN=1 bash install.sh`
Expected: prints prereq checks + "Dry-run complete", exit 0 (dry-run returns before `configure`).

- [ ] **Step 4: Full configure test suite**

Run: `bash setup/tests/test_configure.sh`
Expected: every line `ok - …`, final `0 failures`, exit 0.

- [ ] **Step 5: Commit (only if Step 2 required fixes)**

```bash
git add install.sh
git commit -m "chore(install): shellcheck clean-up for configure stage"
```

---

## Self-Review (completed)

**Spec coverage:** configure stage (Task 5) ✓; `/dev/tty` interactivity + non-interactive fallback (Tasks 2, 5) ✓; per-provider keys with grouping + replace-guard + no-LLM warning + correct target files at 600 (Task 3) ✓; soul Option A copy→rename→editor at `~/.jarvis/SOUL.md` 600 (Task 4) ✓; `prompt_builder.py` untouched (no task — confirmed by cap) ✓; idempotency + `set -u` safety (Tasks 1–5 use `${VAR:-default}`) ✓; testing via shellcheck/bash -n/harness (Task 6) ✓; no service restart (no task touches services) ✓.

**Placeholder scan:** none — every step has runnable code/commands.

**Type/name consistency:** `_env_get`, `_env_upsert`, `_interactive`, `_tty_path`, `_ask`, `_ask_secret`, `_confirm`, `_maybe_set_key`, `configure_api_keys`, `configure_soul`, `configure` used consistently across tasks; `_JARVIS_TTY` fixture var and `INSTALL_DIR`/`HOME` overrides consistent in all tests.

**Notes for the implementer:**
- `stat -c %a` and `sed -i` / `grep -E '\b'` assume GNU coreutils (this repo is Linux-only — fine).
- `set -euo pipefail` in `install.sh` leaks into the sourced test shell; the harness does `set +e +u` after each `source` — keep that.
- Run each task's test from the repo root: `bash setup/tests/test_configure.sh`.
