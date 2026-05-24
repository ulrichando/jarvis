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
response), state in ≤3 lines:

  CHANGED:    <files + one-line why>
  NOT CHANGED: <the OUT list from rule 1, confirmed still untouched>
  VERIFY:     <which suites/checks ran, with result>

This is the audit trail the user reads to spot drift before merging.

## 8. Auto-mod blocklist is load-bearing

The auto-mod loop (`src/voice-agent/pipeline/automod/`) writes proposals
that the user merges manually. The blocklist in
`_state.HARD_BLOCKLIST_PATHS` + the `is_blocked_path()` helper are
referenced from three enforcement layers (spawner prompt,
`finalize.py` diff-check, `bin/jarvis-automod merge` re-validation).
Each layer is independently load-bearing — removing one weakens the
safety story.

Rules:
- Never remove an entry from `HARD_BLOCKLIST_PATHS` without explicit
  user sign-off + a separate spec amendment. Adding entries is fine
  (additive).
- `pipeline/automod/` itself is on the blocklist (no self-referential
  weakening). If `pipeline/automod/_state.py` needs a real refactor,
  human-edit only — auto-mod can never touch it.
- The CLI subprocess wrapper (`bin/jarvis-automod-impl`) is the only
  place the project root is editable from an auto-mod path; its rules
  prompt is part of the safety surface.

If a future auto-mod proposes weakening this rule set, reject the
artifact + audit the pattern detector that emitted the intent.

## Escape hatch

If you need to skip the Stop hook's verification (mid-refactor, tests
intentionally red, debugging the hook itself), set JARVIS_SKIP_VERIFY=1
in Claude Code's parent environment. Requires restarting Claude Code to
take effect — the hook reads env at fire time, but env can't be mutated
mid-session.
