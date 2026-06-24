# Regression Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cross-cutting `.claude/rules/regression-prevention.md` rule and a `Stop`-event verification hook that runs the relevant test suite for files edited during a Claude session and blocks turn-end if any suite fails.

**Architecture:** Two complementary artifacts. The rule (markdown) is process guidance Claude reads before/during work — soft enforcement of "declare scope, don't delete in-use code, don't claim done without evidence." The hook (bash script) is a mechanical gate at turn-end — hard enforcement that runs the right suite per edited subtree (voice-agent → pytest, desktop-tauri → vite build, web → vitest, cli → warn-only) and emits a block-decision JSON when any fail.

**Tech Stack:** Bash, jq, the existing per-subtree test infrastructure (pytest in `src/voice-agent/.venv/`, vite/npm in `src/voice-agent/desktop-tauri/`, vitest in `src/web/`).

**Spec:** [docs/superpowers/specs/2026-05-07-regression-prevention-design.md](../specs/2026-05-07-regression-prevention-design.md)

---

## Task 1: Create the cross-cutting rule file

**Files:**
- Create: `.claude/rules/regression-prevention.md`

- [ ] **Step 1: Write the rule file**

Create `.claude/rules/regression-prevention.md` with this exact content:

```markdown
# Regression prevention — load-bearing process rules

JARVIS has 4 subtrees (voice-agent, desktop-tauri, web, cli) that share
state via shell paths, services (systemd --user), and the bridge on
127.0.0.1:8765. Adding a feature in one tree routinely breaks another.
These rules apply to ALL feature/fix work.

## 1. Declare scope before the first Edit/Write

State this exact shape before touching code:

  SCOPE:    <files / dirs in scope>
  OUT:      <files / areas deliberately not touched>
  WHY OUT:  <one line — what about these is at risk if I drift>

Why: makes "while I'm here…" side-edits visible and auditable BEFORE
they happen. The user can push back when the cost of pushback is one
sentence, not one revert.

## 2. Read the failing path before the first edit

Before editing a function F, read:
- the file containing F
- F's immediate callers (grep the symbol across the repo, not just the subtree)
- F's immediate callees within the same subtree
- any sanitizer / hook / monkey-patch listed in CLAUDE.md that touches F's path

Bounded; not "the whole call graph". Know what relies on F before changing F.

## 3. Shared-code changes — gate only when ≥2 callers diverge

A "shared-code change" is one to a symbol called from ≥2 features that
need DIFFERENT behavior after the change. In that case: keep the old
signature/behavior working, add the new path additively. Do NOT
preemptively gate refactors where all callers want the new behavior —
that's the "no backwards-compat shim" rule from the system prompt.
Decision: count callers. If all want the change, change in place. If
some don't, gate.

## 4. Don't delete code you think is unused

Before removing/renaming any symbol, grep the WHOLE repo including:
- bin/ (shell entry points + cron-style scripts)
- /etc/systemd/user/jarvis-*.service (unit files reference Python paths)
- src/hub/ (HTTP routes called by Chrome extension over WS)
- src/extensions/ (browser-side calls into hub/voice paths)
- /etc/sudoers.d/jarvis (NOPASSWD scopes)

If still unsure: ask. The bridge + hub + Tauri side call into voice-agent
paths via HTTP/IPC and don't show up in a Python-only search.

## 5. Verify before claiming done

"Fixed", "shipped", "works now" require evidence:

- Voice-agent edit → pytest passes. The Stop hook
  (.claude/hooks/verify-before-done.sh) runs this automatically when
  configured. If it isn't, run it yourself.
  Tests passing ≠ feature works. If the change affects live behavior, you
  also need a service restart — but check turn_telemetry.db first
  (CLAUDE.md operational rule: don't restart within 60s of last turn).
- Desktop-tauri edit → `npm run build` (vite, ~7s; catches syntax + import
  errors). For release, ALSO `cargo build --release` (re-embeds dist into
  binary; CLAUDE.md rule).
- UI edit → run the dev server and exercise the path in a browser. Type
  checks verify code, not feature.
- Web / CLI edit → run that tree's check / build command.

If a suite fails, work is NOT done. State the failure plainly; don't
paper over it.

## 6. Mid-flight scope creep — stop, don't expand silently

If you realize during the work that the fix needs files not in the SCOPE
declared at rule 1: stop editing, surface the new files + why they're
needed, get re-approval. Silent scope expansion is the failure mode this
whole rule set exists to prevent.

## 7. End-of-task summary (only on completion, not every reply)

ONLY when a task is complete (not after every tool call, not after every
response — see feedback_no_summaries memory), state in ≤3 lines:

  CHANGED:    <files + one-line why>
  NOT CHANGED: <the OUT list from rule 1, confirmed still untouched>
  VERIFY:     <which suites/checks ran, with result>

This is the audit trail the user reads to spot drift before merging.

## Escape hatch

If you need to skip the Stop hook's verification (mid-refactor, tests
intentionally red, debugging the hook itself), set JARVIS_SKIP_VERIFY=1
in Claude Code's parent environment. Requires restarting Claude Code to
take effect — the hook reads env at fire time, but env can't be mutated
mid-session.
```

- [ ] **Step 2: Verify it's well-formed**

Run: `head -5 .claude/rules/regression-prevention.md`
Expected: shows the title `# Regression prevention — load-bearing process rules` and the first paragraph.

- [ ] **Step 3: Commit**

```bash
git add .claude/rules/regression-prevention.md
git commit -m "add regression-prevention rule to .claude/rules/"
```

---

## Task 2: Create hook scaffold with stdin parsing + guards

**Files:**
- Create: `.claude/hooks/verify-before-done.sh`

- [ ] **Step 1: Write the scaffold**

Create `.claude/hooks/verify-before-done.sh` with this content:

```bash
#!/usr/bin/env bash
# .claude/hooks/verify-before-done.sh
# Stop hook for JARVIS — runs the relevant test suite for files edited
# during a session and blocks turn-end if any suite fails.
# Spec: docs/superpowers/specs/2026-05-07-regression-prevention-design.md

set -uo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)" || REPO_ROOT="$PWD"
cd "$REPO_ROOT" || exit 0

# 1. Read stdin JSON
INPUT="$(cat)"
TRANSCRIPT_PATH="$(jq -r '.transcript_path // empty' <<<"$INPUT")"
STOP_HOOK_ACTIVE="$(jq -r '.stop_hook_active // false' <<<"$INPUT")"

# 2. Recursion guard — never block twice
[[ "$STOP_HOOK_ACTIVE" == "true" ]] && exit 0

# 3. Escape hatch
[[ "${JARVIS_SKIP_VERIFY:-0}" == "1" ]] && exit 0

# 4. Edit detection (filled in by Task 3)
exit 0
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x .claude/hooks/verify-before-done.sh`

- [ ] **Step 3: Static-check with shellcheck**

Run: `shellcheck .claude/hooks/verify-before-done.sh`
Expected: no errors.

- [ ] **Step 4: Test recursion guard**

Run:
```bash
echo '{"transcript_path":"/nonexistent","stop_hook_active":true}' | .claude/hooks/verify-before-done.sh
echo "exit=$?"
```
Expected: `exit=0` (script exits cleanly without trying to read the path).

- [ ] **Step 5: Test escape hatch**

Run:
```bash
JARVIS_SKIP_VERIFY=1 echo '{"transcript_path":"/nonexistent","stop_hook_active":false}' | .claude/hooks/verify-before-done.sh
echo "exit=$?"
```
Expected: `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add .claude/hooks/verify-before-done.sh
git commit -m "scaffold verify-before-done Stop hook with stdin parsing + guards"
```

---

## Task 3: Implement edit detection

**Files:**
- Modify: `.claude/hooks/verify-before-done.sh`

- [ ] **Step 1: Replace the `# 4. Edit detection` placeholder**

Replace the line `# 4. Edit detection (filled in by Task 3)` and the `exit 0` immediately after it with:

```bash
# 4. Edit detection — extract unique edited file paths from transcript JSONL
[[ -z "$TRANSCRIPT_PATH" || ! -f "$TRANSCRIPT_PATH" ]] && exit 0

EDITED_FILES="$(jq -r '
  select(.message.content) | .message.content[]?
  | select(.type=="tool_use" and (.name=="Edit" or .name=="Write" or .name=="MultiEdit"))
  | .input.file_path
' "$TRANSCRIPT_PATH" 2>/dev/null | sort -u)"

[[ -z "$EDITED_FILES" ]] && exit 0

echo "[verify-before-done] DEBUG edits:" >&2
echo "$EDITED_FILES" | sed 's/^/  /' >&2

# 5. Subtree classification (filled in by Task 4)
exit 0
```

- [ ] **Step 2: Build a fixture transcript**

Run:
```bash
cat > /tmp/fixture-transcript.jsonl <<'EOF'
{"type":"user","message":{"role":"user","content":"hi"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"working"},{"type":"tool_use","name":"Edit","id":"t1","input":{"file_path":"/repo/src/voice-agent/jarvis_agent.py","old_string":"a","new_string":"b"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Write","id":"t2","input":{"file_path":"/repo/src/voice-agent/desktop-tauri/src/App.jsx","content":"x"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"MultiEdit","id":"t3","input":{"file_path":"/repo/src/web/components/Foo.tsx"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","id":"t4","input":{"command":"ls"}}]}}
EOF
```

- [ ] **Step 3: Test edit detection against the fixture**

Run:
```bash
echo "{\"transcript_path\":\"/tmp/fixture-transcript.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh 2>&1
```

Expected stderr output (order may vary due to `sort -u`):
```
[verify-before-done] DEBUG edits:
  /repo/src/voice-agent/desktop-tauri/src/App.jsx
  /repo/src/voice-agent/jarvis_agent.py
  /repo/src/web/components/Foo.tsx
```
Expected exit: 0. The Bash tool_use entry is correctly ignored.

- [ ] **Step 4: Test no-edits case**

Run:
```bash
echo '{"type":"user","message":{"role":"user","content":"hi"}}' > /tmp/fixture-noedit.jsonl
echo "{\"transcript_path\":\"/tmp/fixture-noedit.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh
echo "exit=$?"
```
Expected: no DEBUG output, `exit=0`.

- [ ] **Step 5: Static-check**

Run: `shellcheck .claude/hooks/verify-before-done.sh`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add .claude/hooks/verify-before-done.sh
git commit -m "verify-before-done: detect edited files from transcript JSONL"
```

---

## Task 4: Implement subtree classification + suite invocation

**Files:**
- Modify: `.claude/hooks/verify-before-done.sh`

- [ ] **Step 1: Replace the Task 3 placeholder**

Replace the line `# 5. Subtree classification (filled in by Task 4)` and the `exit 0` immediately after it with:

```bash
# 5. Subtree classification
declare -A SUITES=()
WARN_CLI=0
while IFS= read -r f; do
  case "$f" in
    */src/voice-agent/*) SUITES["voice-agent"]=1 ;;
    */src/voice-agent/desktop-tauri/*) SUITES["desktop-tauri"]=1 ;;
    */src/web/*) SUITES["web"]=1 ;;
    */src/cli/*) WARN_CLI=1 ;;
  esac
done <<<"$EDITED_FILES"

# 6. Run suites — collect failures into parallel arrays
FAIL_NAMES=()
FAIL_CMDS=()
FAIL_OUTS=()

run_suite() {
  local name="$1" cmd="$2"
  shift 2
  if ! "$@" >/dev/null 2>&1; then
    echo "[verify-before-done] WARN: $name prereq missing — skipping" >&2
    return 0
  fi
  echo "[verify-before-done] running $name…" >&2
  local out_file
  out_file="$(mktemp)"
  if ! bash -c "$cmd" >"$out_file" 2>&1; then
    FAIL_NAMES+=("$name")
    FAIL_CMDS+=("$cmd")
    FAIL_OUTS+=("$(tail -n 40 "$out_file")")
  fi
  rm -f "$out_file"
}

if [[ -n "${SUITES[voice-agent]:-}" ]]; then
  run_suite "voice-agent" \
    "cd src/voice-agent && .venv/bin/python -m pytest tests/ -x --tb=line --no-header" \
    test -x src/voice-agent/.venv/bin/python
fi
if [[ -n "${SUITES[desktop-tauri]:-}" ]]; then
  run_suite "desktop-tauri" \
    "cd src/voice-agent/desktop-tauri && npm run build" \
    test -d src/voice-agent/desktop-tauri/node_modules
fi
if [[ -n "${SUITES[web]:-}" ]]; then
  run_suite "web" \
    "cd src/web && npm run test" \
    test -x src/web/node_modules/.bin/vitest
fi

# 7. CLI warning (non-blocking)
if [[ "$WARN_CLI" == "1" ]]; then
  echo "[verify-before-done] WARN: CLI files edited. CLAUDE.md says src/cli/ is off-limits without asking — was this intentional?" >&2
fi

# 8. Decision (filled in by Task 5)
exit 0
```

Also remove the `DEBUG edits:` lines added in Task 3 (they were temporary). Find and delete:

```bash
echo "[verify-before-done] DEBUG edits:" >&2
echo "$EDITED_FILES" | sed 's/^/  /' >&2
```

- [ ] **Step 2: Static-check**

Run: `shellcheck .claude/hooks/verify-before-done.sh`
Expected: no errors. (If shellcheck warns about `declare -A`, that's a false positive on associative arrays; suppress with `# shellcheck disable=SC2034` if needed.)

- [ ] **Step 3: Test classification — voice-agent only edits**

Reuse `/tmp/fixture-transcript.jsonl` from Task 3 but replace its contents to contain only voice-agent edits. Easier: edit a known-passing voice-agent test path so the suite actually runs and passes.

Run:
```bash
cat > /tmp/fixture-voice.jsonl <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Edit","id":"t1","input":{"file_path":"/repo/src/voice-agent/sanitizers/dsml.py"}}]}}
EOF

echo "{\"transcript_path\":\"/tmp/fixture-voice.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh
echo "exit=$?"
```

Expected stderr: `[verify-before-done] running voice-agent…` followed by pytest output. Final `exit=0` (assuming the existing voice-agent suite passes).

If pytest takes too long for this manual test, add `JARVIS_SKIP_VERIFY=1` and skip; just confirm classification by adding a temporary `echo "$f matched: ${!SUITES[*]}"` debug line and removing it after.

- [ ] **Step 4: Test CLI warn-only**

Run:
```bash
cat > /tmp/fixture-cli.jsonl <<'EOF'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Edit","id":"t1","input":{"file_path":"/repo/src/cli/some-file.ts"}}]}}
EOF

echo "{\"transcript_path\":\"/tmp/fixture-cli.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh 2>&1
echo "exit=$?"
```

Expected stderr contains: `WARN: CLI files edited. CLAUDE.md says src/cli/ is off-limits without asking — was this intentional?`
Expected `exit=0` (warn is non-blocking).

- [ ] **Step 5: Test prereq-missing path**

Temporarily rename the voice-agent venv:
```bash
mv src/voice-agent/.venv src/voice-agent/.venv.tmp

echo "{\"transcript_path\":\"/tmp/fixture-voice.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh 2>&1
echo "exit=$?"

mv src/voice-agent/.venv.tmp src/voice-agent/.venv
```

Expected stderr: `WARN: voice-agent prereq missing — skipping`. Expected `exit=0` (never block on missing prereq).

- [ ] **Step 6: Commit**

```bash
git add .claude/hooks/verify-before-done.sh
git commit -m "verify-before-done: classify edits by subtree, run matching suites"
```

---

## Task 5: Implement output capture + block-decision JSON

**Files:**
- Modify: `.claude/hooks/verify-before-done.sh`

- [ ] **Step 1: Replace the Task 4 placeholder**

Replace the line `# 8. Decision (filled in by Task 5)` and the `exit 0` immediately after it with:

```bash
# 8. Decision
if [[ ${#FAIL_NAMES[@]} -eq 0 ]]; then
  exit 0
fi

# 9. Build block JSON for Claude Code
REASON="Verification failed before claiming done. Address these and re-run:"
for i in "${!FAIL_NAMES[@]}"; do
  REASON+=$'\n\n--- '"${FAIL_NAMES[$i]}"' (run: '"${FAIL_CMDS[$i]}"') ---\n'"${FAIL_OUTS[$i]}"
done

jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
exit 0
```

- [ ] **Step 2: Static-check**

Run: `shellcheck .claude/hooks/verify-before-done.sh`
Expected: no errors.

- [ ] **Step 3: Test failure path with a deliberately broken suite**

Create a fake test that fails. In `src/voice-agent/tests/`:

```bash
cat > src/voice-agent/tests/test_verify_hook_smoke.py <<'EOF'
def test_intentionally_failing_for_hook_smoke():
    assert False, "smoke test for verify-before-done hook"
EOF
```

Then run the hook with a fixture pointing at any voice-agent file:

```bash
echo "{\"transcript_path\":\"/tmp/fixture-voice.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh
```

Expected stdout (single JSON line): `{"decision":"block","reason":"Verification failed before claiming done. Address these and re-run:\n\n--- voice-agent (run: cd src/voice-agent && .venv/bin/python -m pytest tests/ -x --tb=line --no-header) ---\n... pytest failure trace ..."}`

Verify the JSON parses:
```bash
echo "{\"transcript_path\":\"/tmp/fixture-voice.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh | jq .decision
```
Expected: `"block"`

- [ ] **Step 4: Clean up the fake test**

```bash
rm src/voice-agent/tests/test_verify_hook_smoke.py
```

- [ ] **Step 5: Re-test success path**

Run the same fixture again with the fake test removed:
```bash
echo "{\"transcript_path\":\"/tmp/fixture-voice.jsonl\",\"stop_hook_active\":false}" \
  | .claude/hooks/verify-before-done.sh
echo "exit=$?"
```
Expected: no JSON on stdout, `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add .claude/hooks/verify-before-done.sh
git commit -m "verify-before-done: emit block-decision JSON on suite failure"
```

---

## Task 6: Register the hook in `.claude/settings.json`

**Files:**
- Modify: `.claude/settings.json`

- [ ] **Step 1: Read current settings.json**

Run: `cat .claude/settings.json`

The file currently has only a `permissions` block. We're adding a new top-level `hooks` block.

- [ ] **Step 2: Add the hooks block**

Modify `.claude/settings.json` to add a `hooks` key alongside `permissions`. The full file should become:

```json
{
  "permissions": {
    "allow": [
      "Bash(systemctl --user status *)",
      "Bash(systemctl --user is-active *)",
      "Bash(systemctl --user restart jarvis-voice-agent.service)",
      "Bash(systemctl --user restart jarvis-bridge.service)",
      "Bash(journalctl --user *)",
      "Bash(sqlite3 ~/.local/share/jarvis/turn_telemetry.db *)",
      "Bash(tail * /tmp/jarvis-voice-agent.log)",
      "Bash(tail */tmp/jarvis-voice-agent.log)",
      "Bash(grep * /tmp/jarvis-voice-agent.log)",
      "Bash(.venv/bin/python -m pytest *)",
      "Bash(.venv/bin/python -c *)",
      "Bash(cd src/voice-agent && .venv/bin/python -m pytest *)",
      "Bash(cd src/voice-agent && .venv/bin/python -c *)",
      "Bash(npm run *)",
      "Bash(bun run *)",
      "Bash(bun test*)",
      "Bash(cargo build *)",
      "Bash(cargo check *)",
      "Bash(cargo test *)",
      "Bash(git status*)",
      "Bash(git diff *)",
      "Bash(git log *)",
      "Bash(git show *)",
      "Bash(git branch*)",
      "Bash(rg *)",
      "Bash(time curl *)"
    ]
  },
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/verify-before-done.sh",
            "timeout": 180
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Validate JSON**

Run: `jq . .claude/settings.json > /dev/null && echo OK`
Expected: `OK`. (If jq prints a parse error, fix the syntax.)

- [ ] **Step 4: Commit**

```bash
git add .claude/settings.json
git commit -m "register verify-before-done as Stop hook in .claude/settings.json"
```

---

## Task 7: Smoke-test the installation

**Files:** none (verification only)

- [ ] **Step 1: Confirm rule auto-loads**

Open a new Claude Code session in the repo (new terminal, `cd /home/ulrich/Documents/Projects/jarvis`, `claude`). Ask: "What rule files are loaded for this project?"

Expected: Claude lists `voice-agent.md`, `desktop-tauri.md`, `cli.md`, AND the new `regression-prevention.md`. If the new file isn't shown, the auto-load mechanism didn't pick it up — investigate by checking how the existing rules are loaded (likely via a SessionStart hook or a CLAUDE.md `@import`).

- [ ] **Step 2: Confirm hook fires on Stop**

In a fresh Claude session, ask Claude to make a trivial edit to a voice-agent file (e.g., add a blank line and remove it again, then commit nothing). End the turn.

Expected: in `~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/<session-id>.jsonl` (the active transcript), you should see a Stop hook entry. The voice-agent pytest should have run. Look for `[verify-before-done] running voice-agent…` either in Claude's session output (if the hook's stderr is forwarded) or in any hook-execution log Claude Code maintains.

- [ ] **Step 3: Confirm hook blocks on failure**

In a fresh session, ask Claude to add a deliberately broken test:

```bash
cat > src/voice-agent/tests/test_smoke_block.py <<'EOF'
def test_break(): assert False
EOF
```

Then ask Claude to end its turn ("OK that's done, anything else?"). Expected: the Stop hook fires, pytest fails, the hook emits `decision: "block"` with the failure trace, and Claude is forced to address it instead of ending.

After verifying, clean up:

```bash
rm src/voice-agent/tests/test_smoke_block.py
```

- [ ] **Step 4: Confirm escape hatch works**

In a new shell session, set `JARVIS_SKIP_VERIFY=1` and launch Claude Code: `JARVIS_SKIP_VERIFY=1 claude`. Repeat the deliberately-broken-test from Step 3. Expected: hook is a no-op, turn ends without verification.

After verifying, exit and re-launch Claude without the env var.

- [ ] **Step 5: Document smoke results**

Add a one-line note to the spec file under "Verified during spec phase":
```markdown
- ✅ Smoke test 2026-05-07: hook fires on Stop, blocks on failure, escape hatch works.
```

```bash
git add docs/superpowers/specs/2026-05-07-regression-prevention-design.md
git commit -m "spec: confirm regression-prevention smoke tests passed"
```

---

## Self-review checklist (run after writing the plan)

- ✅ **Spec coverage:**
  - Component 1 (rule file) → Task 1
  - Component 2 (hook script) → Tasks 2–5 (scaffold, edit detect, classify, output)
  - settings.json registration → Task 6
  - Smoke-test (verify after install) → Task 7
- ✅ **No placeholders:** Every step has actual code or actual command. No "implement later", no "fill in details".
- ✅ **Type/name consistency:** `EDITED_FILES`, `SUITES`, `FAIL_NAMES/CMDS/OUTS`, `WARN_CLI`, `JARVIS_SKIP_VERIFY` consistent across tasks. `run_suite` signature stable. Hook command path `.claude/hooks/verify-before-done.sh` consistent in script content and in `settings.json`.
- ✅ **Spec corrections from verify phase incorporated:** desktop-tauri uses `npm run build` (not `typecheck`); web uses `npm run test` (vitest); cli is warn-only; sub-agent guard removed.
