# Plan 003: Delete the dead `handoff_text` sanitizer

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If any
> "STOP conditions" item occurs, stop and report — do not improvise. When done,
> update this plan's status row in `advisor-plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 9861fd11..HEAD -- src/voice-agent/sanitizers/handoff_text.py src/voice-agent/jarvis_agent.py`
> If either file changed since this plan was written, compare the "Current state"
> excerpts below against the live code before proceeding; on a mismatch, treat it
> as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `9861fd11`, 2026-07-02

## Why this matters

`sanitizers/handoff_text.py` is a 234-line import-time monkey-patch that blanks
text on any LLM turn emitting a `transfer_to_*` / `delegate` **tool call**. That
whole subagent/handoff layer was torn down in the 2026-05-20 rebuild — no tool
with those names is registered anymore. A provider only emits a structured
`tool_calls` delta for a **registered** tool, so this sanitizer's trigger is now
structurally unreachable: it is dead code on the fragile import-time patch path
(15 `.install()` calls run before the agent starts; each is a place a typo can
brick boot). Removing it shrinks that surface with zero behavior change. The
residual risk the docs worry about — the model writing a tool-call *shape* into
spoken **text** — is a different layer and stays covered by the `pycall`
sanitizer (it scans text content, not tool-call structure).

## Current state

- `src/voice-agent/sanitizers/handoff_text.py` — the sanitizer. Its own docstring
  (lines 1–3, 27–35) states it patches `inference.llm.LLMStream._parse_choice` and
  fires only when `delta.tool_calls[].function.name` starts with `transfer_to_` or
  equals `delegate`:
  ```
  """Suppress anticipatory text content from supervisor turns that include
  a `transfer_to_*` (or `delegate`) tool call.
  ...
     with `transfer_to_` or equals `delegate`, mark the stream as
     "handoff in progress."
  ```
- `src/voice-agent/jarvis_agent.py:250-252` — the install site (one of the 15
  `.install()` patches):
  ```python
  # of dsml_sanitizer + pycall_sanitizer.
  import sanitizers.handoff_text
  sanitizers.handoff_text.install()
  ```
- **Why removal is safe (the load-bearing fact):** grep confirms `transfer_to_`
  and `delegate` appear only inside the sanitizers that defend against them and in
  docs — **no tool registers those names**. The registry is loaded by
  `tools/_adapter.py::load_all_livekit_tools`; there is no `transfer_to_*` /
  `delegate` tool. The generic tool-call-text leak guard that DOES stay live is
  `sanitizers/pycall.py` (installed at `jarvis_agent.py:241-242`).

### Convention to follow
Sanitizers are idempotent import-time patches; each has an `install()`. Removing
one = delete the module, delete its `import` + `.install()` lines, delete its
dedicated test, and fix the docs that call it load-bearing. Match how the tree
already documents residual guards (comments in `jarvis_agent.py` around the
install block).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Drift check | `git diff --stat 9861fd11..HEAD -- src/voice-agent/sanitizers/handoff_text.py src/voice-agent/jarvis_agent.py` | empty (no drift) |
| Find all references | `grep -rn "handoff_text" src/voice-agent` | only the lines this plan removes |
| Voice test suite | `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` | all pass (~70s, 3000+ tests) |
| Byte-compile the entry | `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('jarvis_agent.py').read())"` | exit 0 |

> Use the voice-agent's own venv at `src/voice-agent/.venv/` — not system Python
> (its `livekit-agents` version is pinned).

## Scope

**In scope** (the only files you should modify/delete):
- `src/voice-agent/sanitizers/handoff_text.py` (delete)
- `src/voice-agent/jarvis_agent.py` (remove the import + install lines only)
- any dedicated test that imports `handoff_text` (delete — see Step 1)
- `CLAUDE.md` and `.claude/rules/voice-agent.md` (remove the now-obsolete
  handoff_text guidance — Step 3)

**Out of scope** (do NOT touch, even though they look related):
- `sanitizers/pycall.py` — LIVE generic leak guard. Leave it (Step 4 is an
  optional, separate one-token cleanup).
- `confab_detector.py` and `test_confab_detector_handoff_rule.py` — a *different*
  residual rule (tool-evidence lookback). Not this plan.
- `jarvis_agent.py:757 inject_handoff_refused_marker` and
  `test_handoff_refused_injection.py` — a different feature (confab marker), not
  the sanitizer. Do not delete.
- Any other `.install()` line — the other 14 are live/provider-critical.

## Git workflow

- Branch: `advisor/003-delete-handoff-text`
- Conventional-commit style (match `git log`), e.g.
  `refactor(voice): delete dead handoff_text sanitizer (unreachable trigger)`
- Do NOT push or open a PR unless the operator asks.

## Steps

### Step 1: Confirm nothing else depends on it, and find its test
```
grep -rn "handoff_text" src/voice-agent
```
Expect matches only in: `sanitizers/handoff_text.py`, `jarvis_agent.py:251-252`,
and possibly one test file. If a test file imports `handoff_text` (e.g.
`tests/test_handoff_text*.py`), add it to the delete list. If `handoff_text` is
imported anywhere **outside** `sanitizers/`, `jarvis_agent.py`, and `tests/` →
**STOP** (unexpected coupling).

**Verify**: the grep output contains no `src/voice-agent` path other than the
module, the install site, and (optionally) a test.

### Step 2: Delete the module + its install lines + its test
- `git rm src/voice-agent/sanitizers/handoff_text.py`
- `git rm` the dedicated test found in Step 1 (if any).
- In `jarvis_agent.py`, delete exactly these two lines (250's comment may be
  merged with the block above — keep surrounding unrelated comments intact):
  ```python
  import sanitizers.handoff_text
  sanitizers.handoff_text.install()
  ```

**Verify**:
- `grep -rn "handoff_text" src/voice-agent` → no matches.
- `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('jarvis_agent.py').read())"` → exit 0.

### Step 3: Remove the obsolete docs guidance
- `.claude/rules/voice-agent.md` — delete the paragraph beginning
  **"`handoff_text_suppressor` walks the FULL chat_ctx"**. It documents a module
  that no longer exists.
- `CLAUDE.md` — remove the `handoff_text.py` bullet in the sanitizers list
  (the "drops anticipatory text content from supervisor turns containing
  `transfer_to_*` / `delegate`" line). Leave the other sanitizer bullets.

**Verify**: `grep -rn "handoff_text" CLAUDE.md .claude/rules/voice-agent.md` → no matches.

### Step 4 (OPTIONAL — only if trivial): drop the dead `task_done` token from `pycall`
Open `sanitizers/pycall.py`. If it contains a literal `task_done` entry in a
blocklist/regex of tool-call names to suppress, remove ONLY that token (leave the
generic `name(...)` / `<function>` / JSON-array handling untouched). `task_done`
was the subagent completion signal — also gone. If the removal is not a clean
single-token edit, **skip this step** (not worth the risk).

**Verify**: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_pycall_sanitizer.py tests/test_pycall_synthesis_integration.py -q` → all pass.

### Step 5: Full suite
```
cd src/voice-agent && .venv/bin/python -m pytest tests/ -q
```
**Verify**: all pass. A green suite here is the proof that no live path depended
on `handoff_text`.

## Test plan

- No new tests. This is a deletion; the existing suite is the regression net.
- If Step 1 found a dedicated `handoff_text` test, it is deleted with the module
  (it only tested the removed patch).
- The suite must stay green with the same pass count minus any deleted test's cases.

## Done criteria

ALL must hold:
- [ ] `grep -rn "handoff_text" src/voice-agent CLAUDE.md .claude/rules/` → no matches
- [ ] `git rm` removed `sanitizers/handoff_text.py` (+ its test if any)
- [ ] `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` exits 0, all pass
- [ ] `git status` shows only the in-scope files changed/deleted
- [ ] `advisor-plans/README.md` status row updated to DONE

## STOP conditions

Stop and report (do not improvise) if:
- The drift check shows `handoff_text.py` or the `jarvis_agent.py` install block
  changed since `9861fd11` and the excerpts no longer match.
- `grep` finds `handoff_text` imported outside `sanitizers/` + `jarvis_agent.py`
  + `tests/` (unexpected live dependency).
- The full suite fails after the deletion, even once — the "unreachable trigger"
  assumption may be wrong. Report the failing test; do not patch around it.

## Maintenance notes

- Reviewer should confirm the remaining 14 `.install()` calls are untouched
  (only the `handoff_text` pair is removed) and that `pycall` is intact.
- If a future change ever reintroduces a `transfer_to_*` / `delegate` tool
  (it should not — see `.claude/rules/voice-agent.md` "No subagent layer"), the
  text-leak concern is handled by `pycall` + the supervisor prompt leak-guard, not
  by resurrecting this module.
- This removes one of the "residual defense" patches the docs mention; the
  parallel residual rules in `confab_detector.py` are intentionally left in place.
