# Plan 006: First `jarvis_agent.py` extraction wave — the thinking-heartbeat cluster

> **Executor instructions**: This is a careful refactor of the single most
> load-bearing file in the voice agent (8,176 lines). Follow step by step, run
> every verification, and **STOP and report** at the first sign the coupling is
> larger than documented — do NOT improvise your way through a monolith. When
> done, update this plan's row in `advisor-plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 9861fd11..HEAD -- src/voice-agent/jarvis_agent.py`
> `jarvis_agent.py` changes often. If the line numbers below no longer point at
> the named functions, re-locate them by name with `grep -n "def _mark_tool_start"
> src/voice-agent/jarvis_agent.py` and use the found locations; if the functions
> are gone/renamed, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: L
- **Risk**: MED-HIGH
- **Depends on**: none (but do it LAST, alone — it is the least reversible)
- **Category**: tech-debt
- **Planned at**: commit `9861fd11`, 2026-07-02

## Coupling analysis (done 2026-07-03 — read before executing)

The cluster is NOT a clean lift; it mixes two concerns, and only one is
separable:
- **Concern #1 — tray file-flags (EXTRACT):** `_mark_tool_start`/`_mark_tool_end`
  (write/unlink `~/.jarvis/.tool-running`), `_mark_thinking_start`/`_mark_thinking_end`
  (`~/.jarvis/.agent-thinking`), `_start_thinking_heartbeat`/`_cancel_thinking_heartbeat`,
  `_thinking_idle_grace_s`/`_thinking_max_idle_s` (return env-derived floats — no
  module constants), `_bump_turn_activity`, `_schedule_idle_heartbeat_cancel`,
  `_cancel_pending_idle_heartbeat_cancel` — **11 functions** + the 2 path
  constants `_TOOL_BUSY_FILE`, `_AGENT_THINKING_FILE`. Verified: `_mark_tool_start`
  does NOT touch the counter (only writes the tray file). These depend only on
  logger/time/Path/os/asyncio → clean to move to `pipeline/thinking_heartbeat.py`.
- **Concern #2 — chain counter (LEAVE IN PLACE):** `_tool_calls_this_turn`,
  `_TURN_TOOL_CALL_LIMIT`, `_reset_tool_call_count`. This is `run_jarvis_cli`'s
  chain-limit, entangled with the counter increment/check at `jarvis_agent.py`
  ~line 1737 and the telemetry read at ~7410. It is a DIFFERENT concern that
  happens to be co-located; do NOT move it. (Note: the session attribute
  `session._jarvis_tool_calls_this_turn` is a LIST — unrelated to the int
  counter despite the similar name; also leave it.)

So the revised scope: extract the **11 tray-flag functions + 2 path constants**
only. This is genuinely cohesive and low-coupling once the counter is excluded.

**Blocked on execution 2026-07-03:** a voice session was active (last turn 33s
ago). This plan's Step 5 live smoke needs a restart, which the CLAUDE.md rule
forbids within 60s of a turn. Do this in an idle window so the tray-flag smoke
can run — a green unit suite alone does NOT prove the tray file-writes still fire.

## Why this matters

`src/voice-agent/jarvis_agent.py` is **8,176 lines** and still growing — it sat at
~5,300 right after the 2026-05-10 "10/10 refactor" that was supposed to bound it.
It is edited in nearly every session; a monolith this size is where merge pain,
review blind spots, and accidental breakage concentrate. The fix is not one heroic
split — it is repeatable, verifiable **waves**, each extracting one cohesive
cluster into `pipeline/`. This plan does the **first wave** and establishes the
pattern: the "thinking heartbeat" telemetry cluster (~225 lines, one clear
concern, a small enumerable coupling set). Success here is a template; it is
deliberately conservative so the pattern is proven safe before larger waves.

## Current state

The target is a cohesive group of 12 functions at `jarvis_agent.py:1184-1409`, all
concerned with the "is JARVIS thinking / running a tool" heartbeat that drives the
tray indicator and idle detection:

```
_mark_tool_start (1184)          _thinking_max_idle_s (1336)
_mark_tool_end (1195)            _bump_turn_activity (1344)
_mark_thinking_start (1211)      _schedule_idle_heartbeat_cancel (1355)
_mark_thinking_end (1221)        _cancel_pending_idle_heartbeat_cancel (1389)
_start_thinking_heartbeat (1278) _reset_tool_call_count (1409)
_cancel_thinking_heartbeat (1295)
_thinking_idle_grace_s (1315)
```

**Their shared module state (the coupling set — the whole risk of this plan):**
- `_AGENT_THINKING_FILE`, `_TOOL_BUSY_FILE` — file-path constants (write targets
  for the tray). Move WITH the cluster.
- `_THINKING_MAX_IDLE_S`, `_THINKING_IDLE_GRACE_S`, `_TURN_TOOL_CALL_LIMIT` —
  numeric constants. Move WITH the cluster (or read from `config` if that is where
  they are sourced — confirm in Step 1).
- `_tool_calls_this_turn` — a **mutable module counter** (`global` in
  `_reset_tool_call_count` and mutated in `_mark_tool_start`). This is the one
  piece of shared state that other code may read. Step 1 finds every reader.

### Convention to follow
Extracted concerns live under `pipeline/` as their own module (see how
`pipeline/turn_router.py`, `pipeline/turn_telemetry.py`, `pipeline/prosody.py`
were extracted). The new module exposes plain functions; `jarvis_agent.py` imports
them. Match the existing extracted-module docstring style (top-of-file summary of
what moved and why).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Locate a function | `grep -n "def _mark_tool_start" src/voice-agent/jarvis_agent.py` | current line |
| Find state readers | `grep -rn "_tool_calls_this_turn\|_AGENT_THINKING_FILE\|_TOOL_BUSY_FILE\|_start_thinking_heartbeat" src/voice-agent --include='*.py'` | enumerated call sites |
| Parse-check | `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('jarvis_agent.py').read()); ast.parse(open('pipeline/thinking_heartbeat.py').read())"` | exit 0 |
| Import-check | `cd src/voice-agent && .venv/bin/python -c "import pipeline.thinking_heartbeat"` | exit 0, no error |
| Full suite | `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` | all pass (~70s) |
| Live smoke | see Step 5 (restart + one turn) | agent speaks; tray indicator toggles |

> Use `src/voice-agent/.venv/`.

## Scope

**In scope**:
- Create `src/voice-agent/pipeline/thinking_heartbeat.py` (the 12 functions + their
  constants + the counter, as module state of the new file).
- Edit `src/voice-agent/jarvis_agent.py`: remove the moved definitions, add an
  import, and route any in-file callers through the new module.
- Route any OTHER file that reads the moved state through the new module (found in
  Step 1 — expected to be few or none).
- A new `tests/test_thinking_heartbeat.py` if the cluster has extractable pure
  logic to characterize (Step 4).

**Out of scope** (do NOT touch):
- Any of the 15 `.install()` monkey-patch lines and the sanitizers/resilience they
  install — unrelated, load-bearing.
- The `JarvisAgent` class (line ~4250) beyond updating its calls to the moved
  functions.
- Any OTHER function cluster in `jarvis_agent.py` — this is ONE wave. Do not
  "while I'm here" extract more; that is a separate future wave.
- Behavior changes of any kind. This is a pure move — same logic, new home.

## Git workflow

- Branch: `advisor/006-extract-thinking-heartbeat`
- One commit for the extraction; conventional style:
  `refactor(voice): extract thinking-heartbeat telemetry to pipeline/thinking_heartbeat.py`
- Do NOT push/PR unless the operator asks.

## Steps

### Step 1: Map the coupling BEFORE moving anything (the go/no-go gate)
```
grep -rn "_tool_calls_this_turn\|_AGENT_THINKING_FILE\|_TOOL_BUSY_FILE\|_THINKING_MAX_IDLE_S\|_THINKING_IDLE_GRACE_S\|_TURN_TOOL_CALL_LIMIT" src/voice-agent --include='*.py'
grep -rn "_mark_tool_start\|_mark_tool_end\|_mark_thinking_start\|_mark_thinking_end\|_start_thinking_heartbeat\|_cancel_thinking_heartbeat\|_bump_turn_activity\|_reset_tool_call_count\|_schedule_idle_heartbeat_cancel\|_cancel_pending_idle_heartbeat_cancel" src/voice-agent --include='*.py'
```
Write down every call site. Expected: all inside `jarvis_agent.py`. Also confirm
where the constants are *defined* — if `_THINKING_MAX_IDLE_S` etc. come from a
`config` module rather than a literal in `jarvis_agent.py`, the new module should
import them from that same source, not redefine them.

**Verify / STOP gate**: if `_tool_calls_this_turn` or any moved function is
referenced from **more than 2 files**, or from a file you cannot cleanly route
through the new module, **STOP and report the coupling map** — the safe first wave
may need to be a different, smaller cluster. Proceeding past heavy coupling is how
a monolith refactor breaks the agent.

### Step 2: Create the new module
Create `src/voice-agent/pipeline/thinking_heartbeat.py`:
- Top docstring: what moved and why (mirror `pipeline/turn_router.py`'s header
  style).
- Move the 12 functions verbatim (logic unchanged).
- Move the constants they own (`_AGENT_THINKING_FILE`, `_TOOL_BUSY_FILE`, and the
  idle/limit constants unless Step 1 showed they belong in `config`).
- Keep `_tool_calls_this_turn` as module state of the NEW file. Expose the two
  operations other code needs on it as functions (it already has
  `_reset_tool_call_count()` and the `_mark_tool_*` mutators — if any external
  reader needs the *value*, add a `tool_calls_this_turn() -> int` accessor rather
  than exporting the raw global).

**Verify**: `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('pipeline/thinking_heartbeat.py').read())"` → exit 0.

### Step 3: Rewire `jarvis_agent.py`
- Delete the 12 moved function definitions and the moved constants from
  `jarvis_agent.py`.
- Add near the other `pipeline` imports:
  `from pipeline.thinking_heartbeat import (_mark_tool_start, _mark_tool_end, ...)`
  importing exactly the names still called in-file (from Step 1's call-site list).
  (Keeping the leading-underscore names on import preserves every existing call
  site unchanged.)
- Update any external reader found in Step 1 to import from the new module.

**Verify**:
- `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('jarvis_agent.py').read())"` → exit 0.
- `cd src/voice-agent && .venv/bin/python -c "import pipeline.thinking_heartbeat"` → exit 0.
- `grep -n "def _mark_tool_start" src/voice-agent/jarvis_agent.py` → **no match**
  (moved out), and `grep -n "_mark_tool_start" src/voice-agent/jarvis_agent.py` →
  only the import + call sites remain.

### Step 4: Characterization test (small, optional-but-preferred)
If any moved function is pure enough to test in isolation (e.g.
`_thinking_idle_grace_s` / `_thinking_max_idle_s` returning env/config-derived
numbers, or the counter reset/mutate cycle), add `tests/test_thinking_heartbeat.py`
modeled on an existing small unit test (e.g. `tests/test_tool_name_sanitizer.py`
structure). Cover: counter increments on `_mark_tool_start`, resets on
`_reset_tool_call_count`. If nothing is cleanly unit-testable without heavy mocking,
skip this step and rely on the full suite.

**Verify**: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_thinking_heartbeat.py -q` → pass (if written).

### Step 5: Full suite + live smoke
```
cd src/voice-agent && .venv/bin/python -m pytest tests/ -q
```
**Verify**: all pass.

Then a live smoke — **respect the restart rule**: check
`~/.local/share/jarvis/turn_telemetry.db` for the latest `ts_utc`; if within 60s a
session is active — do NOT restart, report that the live smoke is deferred. If idle:
```
systemctl --user restart jarvis-voice-agent.service
# then speak one command to JARVIS (or the operator does) that triggers a tool,
# and confirm: it responds AND the tray "thinking/tool" indicator toggles.
```
**Verify**: agent responds and the tray indicator (driven by `_AGENT_THINKING_FILE`
/ `_TOOL_BUSY_FILE`) still toggles — proves the moved file-writes still fire.

## Test plan

- The full existing suite (3,000+ tests) is the primary regression net — a pure
  move must not change any result.
- Optional new `tests/test_thinking_heartbeat.py` characterizes the counter cycle
  so a future wave can't silently break it.
- The live smoke is the only way to confirm the tray file-writes still fire (they
  are side effects the unit suite may not exercise).

## Done criteria

ALL must hold:
- [ ] `pipeline/thinking_heartbeat.py` exists with the 12 functions; `jarvis_agent.py`
      no longer defines them
- [ ] `cd src/voice-agent && .venv/bin/python -c "import pipeline.thinking_heartbeat"` exits 0
- [ ] `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` exits 0, all pass
- [ ] `jarvis_agent.py` line count dropped by ~200 (`wc -l` before/after)
- [ ] Live smoke passed OR explicitly deferred with reason (active session)
- [ ] `git status` shows only in-scope files changed
- [ ] `advisor-plans/README.md` status row updated

## STOP conditions

Stop and report (do not improvise) if:
- Step 1 shows the coupling set spans >2 files or an un-routable reader.
- The moved functions reference module state NOT in the documented coupling set
  (an undocumented global) — report it; do not guess its ownership.
- The full suite fails and the failure is in the moved cluster's behavior.
- A circular import appears (`jarvis_agent` ↔ `thinking_heartbeat`) — report the
  cycle; the fix is to pass state in, not to paper over with deferred imports
  without review.
- The drift check shows the functions were already moved/renamed since `9861fd11`.

## Maintenance notes

- This is **wave 1** of an ongoing effort. Once proven, the next candidate waves
  (each its own plan): the session-handler registration trio
  (`_register_session_error_handlers` / `_register_session_crash_watchdog` /
  `_register_state_tracking_handlers`, ~5333-5720 — higher closure-coupling, do
  after this), and the quiet-hours/ambient/addressed heuristics (~616-716).
- Reviewer should confirm this is a **pure move** — diff the moved functions against
  the archive to prove byte-for-byte logic, only the home changed.
- Do not let this wave grow: one cluster per plan keeps each reviewable and each
  green suite meaningful.
