# Regression-prevention rules for `.claude/` — design

**Date:** 2026-05-07
**Author:** Ulrich + Claude (brainstorming session)
**Status:** Approved design, ready for implementation plan

## Problem

When Claude adds a feature or fixes a bug in JARVIS, working functionality elsewhere routinely breaks. Concrete failure modes Ulrich has hit:

1. **Touched unrelated code.** Side-edits ("while I'm here…") break things outside the declared scope.
2. **Shared-code change broke old callers.** A modification to a shared function/route works for the new path but silently breaks an older flow that depended on the old behavior.
3. **Removed/renamed something still in use.** Code looked dead but was called by another subtree, a hook, a systemd unit, or a shell script in `bin/`.
4. **Claimed done without verifying.** "Fixed" / "shipped" without running tests, restarting the relevant service, or actually exercising the feature.

The existing memory `feedback_no_regressions.md` documents the pattern but isn't preventing it. Memory is read inconsistently; rules in `.claude/` load deterministically every session.

## Goal

Make regressions visible **before** they ship, without adding so much friction that legitimate cross-cutting changes become painful.

## Non-goals

- A scope-guard that blocks edits outside a declared file list. Too brittle — many legitimate fixes touch multiple files.
- Running full release builds (`cargo build --release`, web production build) on every Stop. Too slow.
- Replacing CLAUDE.md or the per-subtree rules. They cover load-bearing technical constraints; this is process.

## Architecture

Three artifacts under `.claude/`:

```
.claude/
  rules/
    regression-prevention.md   ← NEW: cross-cutting process rules
  hooks/
    verify-before-done.sh      ← NEW: Stop hook
  settings.json                ← MODIFIED: register the Stop hook
```

`.claude/rules/*.md` already auto-loads in every session (see existing `voice-agent.md`, `desktop-tauri.md`, `cli.md`). The new file picks up the same loading mechanism — no CLAUDE.md import wiring needed, but verify after install with a fresh session.

The rule and the hook are complementary:

- The **rule** is process guidance Claude reads before/during work. Soft. Catches "touched unrelated code", "shared-code change broke callers", "removed something in use".
- The **hook** is a mechanical gate at turn-end. Hard. Catches "claimed done without verifying" by running the relevant test suite(s) and refusing to let the turn end if they fail.

## Component 1 — `.claude/rules/regression-prevention.md`

Cross-cutting process rules. Loads in every session regardless of which subtree Claude is working in. Contents:

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

## Component 2 — `.claude/hooks/verify-before-done.sh`

Stop-event hook. Bash script. Receives JSON on stdin, decides whether to block, exits 0 (allow) or returns a JSON `{ "decision": "block", "reason": "..." }` on stdout.

### Algorithm

1. Read stdin JSON. Extract `transcript_path` and `stop_hook_active`.
2. **Recursion guard.** If `stop_hook_active === true`, exit 0 immediately. (Prevents infinite loops when tests stay red.)
3. **Escape hatch.** If `JARVIS_SKIP_VERIFY=1` in env, exit 0.
4. **Edit detection.** Parse `transcript_path` (JSONL) with `jq` — filter verified against actual transcripts:
   ```
   jq -r 'select(.message.content) | .message.content[]?
          | select(.type=="tool_use" and (.name=="Edit" or .name=="Write" or .name=="MultiEdit"))
          | .input.file_path' "$transcript_path" | sort -u
   ```
5. **Subtree mapping.** For each unique edited path, classify (verified against actual `package.json` files in repo):

   | Path prefix | Suite |
   |---|---|
   | `src/voice-agent/` | `cd src/voice-agent && .venv/bin/python -m pytest tests/ -x --tb=line --no-header` |
   | `src/voice-agent/desktop-tauri/` | `cd src/voice-agent/desktop-tauri && npm run build` — vite build, ~7s on this repo. desktop-tauri is JSX/JS (no TS, no eslint config); vite build catches syntax errors, broken imports, missing exports. Side effect: rewrites `dist/` (gitignored). If the user is mid-manual-dist-test, they use the escape hatch |
   | `src/web/` | `cd src/web && npm run test` — vitest. Web has no standalone typecheck script; vitest is the real regression gate that exists |
   | `src/cli/` | **warn-only.** No suite to run (placeholder `test` script). Per CLAUDE.md, CLI is off-limits without asking; if hook sees a CLI edit, emit a non-blocking warning ("CLAUDE.md says CLI is off-limits without asking — was this intentional?") but exit 0 |
   | `src/hub/` | no-op (verified: hub has no test files) |
   | anything else | no-op for this hook |

   **Note on sub-agent stops:** Claude Code emits `SubagentStop` as a separate event from `Stop`. The hook is registered on `Stop` only, so sub-agent turn-ends won't trigger it. No sub-agent guard logic is needed.

6. **Prerequisite check.** For each suite to run, check the prerequisites exist:
   - voice-agent: `src/voice-agent/.venv/bin/python` is executable (verified: present in repo)
   - desktop-tauri: `src/voice-agent/desktop-tauri/node_modules/` exists (npm install has been run)
   - web: `src/web/node_modules/.bin/vitest` exists
   If a prerequisite is missing, log a one-line warning to stderr and skip that suite. **Never block on a missing prerequisite** — broken hook is worse than no hook.
7. **Run suites.** Run sequentially. Capture combined stdout+stderr. Track which (if any) fail.
8. **Output handling.** On any failure:
   - For pytest / vitest failures: capture the last 40 lines of combined output (the failure trace).
   - For typecheck failures: capture the full output (typically short).
   - Emit `{"decision": "block", "reason": "…"}` to stdout. The reason includes which suite failed and the captured output, prefixed with a one-line action hint ("Run `<command>` and address the failures before claiming done.").
9. **On all suites passing or no edits detected:** exit 0.

### settings.json registration

Add (or merge into) `.claude/settings.json`:

```json
{
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

`timeout: 180` because voice-agent pytest is ~25s, plus desktop-tauri vite build (~7s measured), plus web vitest (variable; could be 30–60s depending on test count), plus startup overhead. 180s gives comfortable margin without being so long that a hung suite blocks the user indefinitely. Re-tune after the first week of real use.

The hook script needs `chmod +x` after creation.

### What the hook does NOT do

- No `cargo build --release` (minutes — too slow for Stop). Release verification stays manual per CLAUDE.md.
- No service restart. Restart safety is the user's call (telemetry-DB-based 60s check).
- No browser/UI exercise. Type-check is the cheap baseline; actual feature exercise is on Claude per rule 5.
- No re-running of previously-passed suites within the same turn (each Stop gets one verification pass).

## Verified during spec phase

- ✅ `jq` is installed at `/usr/bin/jq`.
- ✅ JSONL transcript shape — `jq` filter returns the right (name, file_path) pairs against an actual transcript.
- ✅ `src/voice-agent/.venv/bin/python` exists.
- ✅ `src/web/` has no `typecheck` script — `test` (vitest) is the regression gate that exists. Mapping updated.
- ✅ `src/voice-agent/desktop-tauri/` is JSX/JS (no TypeScript, no eslint config) — `npm run build` (vite, ~7s) is the realistic regression gate. No package.json changes needed.
- ✅ `src/cli/` has no real test command — hook downgrades to warn-only on CLI edits.
- ✅ `src/hub/` has no test files — confirmed no-op.
- ✅ Sub-agent stops use a separate `SubagentStop` event in Claude Code; registering on `Stop` only avoids the issue.

## Open — verify after install

- After install, open a fresh Claude session in the repo and confirm the new `regression-prevention.md` shows up in project instructions context. (`.claude/rules/*.md` auto-load mechanism inferred from existing files; not verified for the new file specifically.)
- Confirm Claude Code's exact Stop-hook stdin schema (field names: `transcript_path`, `stop_hook_active`). Inferred from documented behavior; verify by logging stdin on first hook fire.

## Risks

- **Hook breaks the Stop event.** A bash bug, missing `jq`, or wrong stdin parsing could prevent any turn from ending. Mitigation: prerequisite checks (#6) and the recursion guard (#2). The escape-hatch env var is the manual override.
- **False blocks.** A flaky test (network, timing) blocks legitimate completion. Mitigation: `JARVIS_SKIP_VERIFY=1`, plus rule 5 documents the override.
- **Hook adds 30–60s to every turn that touches code.** Acceptable trade-off given the regression cost. Conversational/no-edit turns short-circuit immediately at step 4 (no edits → no suites → exit 0 fast).

## Out of scope (explicitly)

- Per-task SCOPE manifest enforced by a PreToolUse hook. Considered, rejected — too brittle.
- Replacing or modifying the existing `feedback_no_regressions.md` memory. The memory and the rule serve different roles (memory = "what user wants", rule = "what to do"); both stay.
- Adding a similar hook for Misty Scone (`src/os/desktop/`). Separate codebase per CLAUDE.md, can be added later if the same regressions hit there.
- Cross-cutting integration tests (e.g., does a voice-agent change break the hub's HTTP routes?). The hook only runs in-tree tests; integration coverage is a separate, larger initiative.
