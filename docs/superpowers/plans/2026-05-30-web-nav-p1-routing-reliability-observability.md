# Web-Nav Phase 1 (Routing + Reliability + Observability) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JARVIS route web tasks to its real browser agent (browser-use via `browser_task`),
make that path reliable using browser-use's own (currently-ignored) knobs, and make it observable —
with **no model changes, no new dependencies, no re-architecture**.

**Architecture:** Three independent slices. (A) Prompt/schema edits steer the model: headless
`browser_task` for web data/nav, visible `computer_use` only when on-screen effect is required, raw
clicking last. (B) `browser_use_bridge/runner.py` constructs the `browser_use.Agent` — wire the
reliability params it ignores, **but only those that exist in the pinned 0.12.6** (Task 1 gates this).
(C) Surface browser-use's per-step trace into `turn_telemetry.db` for post-mortem debugging.

**Tech Stack:** Python 3.13, the isolated browser-use venv (`~/.jarvis/browser-use-venv`, browser-use
0.12.6), stdlib `sqlite3`, `pytest`. Voice-agent venv for tests: `src/voice-agent/.venv/bin/python`.
Spec: `docs/superpowers/specs/2026-05-30-web-nav-enterprise-program-roadmap.md` (Phase 1 design).

---

## File Structure
- Modify: `src/voice-agent/browser_use_bridge/runner.py` — Agent construction (wire confirmed params).
- Modify: `src/voice-agent/tools/browser.py` — schema description, adaptive `max_steps`, task validation, error path (stderr tail), step-trace surfacing.
- Modify: `src/voice-agent/tools/computer_use.py` — schema description ("When NOT to use") only.
- Modify: `bin/jarvis-gemini-tools`, `bin/jarvis-gpt-tools` — inject routing rules into the OPS block; delete the contradictory "computer_use web fallback" line in gpt-tools.
- Modify: `src/voice-agent/prompts/supervisor.md` — add/clarify the tool-priority ladder (source of truth for the rules injected into direct modes).
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` — add `browser_task_steps` table (additive).
- Create: `src/voice-agent/browser_use_bridge/PARAMS_0_12_6.md` — recorded introspection result (Task 1).
- Tests: `src/voice-agent/tests/test_browser_task_reliability.py` (adaptive max_steps + task validation, pure).

---

### Task 1: GATE — introspect browser-use 0.12.6's Agent params

**Files:** Create `src/voice-agent/browser_use_bridge/PARAMS_0_12_6.md`.

Nothing downstream may pass a kwarg unconfirmed here. The docs reflect a newer release; trust the package.

- [ ] **Step 1.1: Run the introspection**

```bash
~/.jarvis/browser-use-venv/bin/python - <<'PY'
import browser_use, inspect, json
print("browser_use version:", getattr(browser_use, "__version__", "?"))
from browser_use import Agent
sig = inspect.signature(Agent.__init__)
want = ["use_vision","max_failures","llm_timeout","step_timeout","fallback_llm",
        "calculate_cost","sensitive_data","allowed_domains","max_steps","step_timeout_seconds"]
params = set(sig.parameters)
print(json.dumps({p: (p in params) for p in want}, indent=2))
# Also show where max_steps lives (Agent.run vs __init__)
runsig = inspect.signature(Agent.run)
print("Agent.run params:", list(runsig.parameters))
PY
```

- [ ] **Step 1.2: Record the result** verbatim into `PARAMS_0_12_6.md` (the JSON map of param→present, the version, and whether `max_steps` is an `__init__` arg or an `Agent.run(max_steps=...)` arg). Tasks 3 use ONLY params marked present. If a param is absent, note its 0.12.6 equivalent if any (e.g., `step_timeout` vs `step_timeout_seconds`), else skip it.
- [ ] **Step 1.3: Commit**

```bash
git add src/voice-agent/browser_use_bridge/PARAMS_0_12_6.md
git commit -m "chore(browser): record browser-use 0.12.6 Agent param surface (reliability-wiring gate)"
```

---

### Task 2: Routing fix (prompt/schema text only)

**Files:** Modify `src/voice-agent/tools/browser.py` (description), `src/voice-agent/tools/computer_use.py`
(description), `bin/jarvis-gemini-tools`, `bin/jarvis-gpt-tools`, `src/voice-agent/prompts/supervisor.md`.
Pure text edits — **no schema-shape changes** (keeps the `anthropic_strict_schema` patch untouched).

- [ ] **Step 2.1:** In `tools/browser.py`, rewrite the `browser_task` registration `description` to LEAD with the headless signal. Read the current description first; replace its opening with:

> "Drive a REAL web browser **headlessly in the background** to do a web task end-to-end, then report
> back a short text summary (there is NO visible window — the user does not watch it work). Use this
> for any web data/navigation goal: look something up, search a site, read/compare pages, fill a form,
> post/submit. Examples: 'check the top Hacker News stories', 'find the price of the RTX 6000 on
> nvidia.com and tell me', 'log into X and read my latest DMs'. Prefer this over computer_use for
> anything where the goal is information or web actions rather than showing something on the screen."

- [ ] **Step 2.2:** In `tools/computer_use.py`, append a "When NOT to use" sentence to its `description`:

> "When NOT to use: for web lookups or web navigation where nothing needs to appear on the user's own
> screen, prefer `browser_task` (headless, DOM-aware, more reliable). Use computer_use for the VISIBLE
> desktop — showing something on screen, controlling a native GUI app, or when the user explicitly
> wants to watch it happen."

- [ ] **Step 2.3:** In `src/voice-agent/prompts/supervisor.md`, add/clarify a **Tool-priority ladder**
  block near the existing tool-routing guidance:

> **Web/desktop tool ladder (use the highest that fits):** 1) a dedicated API/tool if one exists →
> 2) `browser_task` (headless browser agent) for web data/navigation → 3) `computer_use` to control
> the VISIBLE screen, and only when an on-screen effect is required → 4) raw clicks/keystrokes only as
> a last resort. For "find/look up/check/search/read X on the web" use `browser_task`, NOT computer_use.

- [ ] **Step 2.4:** In `bin/jarvis-gemini-tools` and `bin/jarvis-gpt-tools`, inject the same ladder +
  the visible-vs-headless distinction into the OPS_BLOCK system-prompt text (read each file's OPS block;
  add the rule). In `bin/jarvis-gpt-tools`, **delete the line that teaches `computer_use` as a web
  fallback** (grep for `computer_use` in the OPS/prompt text and remove the web-fallback guidance),
  replacing it with the ladder.
- [ ] **Step 2.5:** `src/voice-agent/.venv/bin/python -m py_compile bin/jarvis-gemini-tools bin/jarvis-gpt-tools` → clean.
- [ ] **Step 2.6: Commit**

```bash
git add bin/jarvis-gemini-tools bin/jarvis-gpt-tools src/voice-agent/tools/browser.py src/voice-agent/tools/computer_use.py src/voice-agent/prompts/supervisor.md
git commit -m "feat(web-nav): route web tasks to browser_task; tool-priority ladder; kill computer_use web-fallback"
```

---

### Task 3: `browser_task` reliability (wire CONFIRMED params + adaptive steps + validation + legible failures)

**Files:** Modify `src/voice-agent/browser_use_bridge/runner.py`, `src/voice-agent/tools/browser.py`.
Create `src/voice-agent/tests/test_browser_task_reliability.py`.

- [ ] **Step 3.1: Failing tests** (pure helpers in `browser.py`, no subprocess):

```python
# test_browser_task_reliability.py
from voice_agent_path import *  # if needed; else import directly
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location("browser_tool",
    pathlib.Path("src/voice-agent/tools/browser.py"))
# NOTE: browser.py imports only stdlib + registry, safe to import in the voice venv.

def test_adaptive_max_steps_lookup_vs_flow(browser_tool):
    assert browser_tool._adaptive_max_steps("find the price of X on nvidia.com") <= 20
    assert browser_tool._adaptive_max_steps(
        "log into the site, add 3 items to the cart, fill checkout and pay") >= 40

def test_task_validation_rejects_destinationless(browser_tool):
    ok, _ = browser_tool._validate_task("just look it up")
    assert ok is False
    ok, _ = browser_tool._validate_task("go to nvidia.com and find the RTX 6000 price")
    assert ok is True
```
(Provide a `browser_tool` fixture that loads `tools/browser.py` via importlib; it imports cleanly because the module pulls only stdlib + registry.)

- [ ] **Step 3.2:** Run → FAIL (helpers undefined).
- [ ] **Step 3.3: Implement helpers in `browser.py`:**

```python
import re
_FLOW_VERBS = re.compile(r"\b(log ?in|sign ?in|checkout|add to cart|fill|submit|book|purchase|pay|"
                         r"compare|apply|register|upload|download|reply|post)\b", re.I)
_DEST = re.compile(r"https?://|\b[\w-]+\.(com|org|net|io|gov|edu|co|ai|dev)\b", re.I)

def _adaptive_max_steps(task: str, override: "int|None" = None) -> int:
    if override:
        return int(override)
    n = len(_FLOW_VERBS.findall(task or ""))
    return 50 if n >= 2 else (35 if n == 1 else 15)

def _validate_task(task: str) -> "tuple[bool,str]":
    t = (task or "").strip()
    if len(t) < 8:
        return False, "task too short / no clear goal"
    if not _DEST.search(t) and not re.search(r"\b(search|google|find|look up|on the web|website)\b", t, re.I):
        return False, "no destination URL or clear web target — refine the task"
    return True, ""
```

- [ ] **Step 3.4:** In `tools/browser.py::_handle_browser_task`, call `_validate_task` before spawning
  (return a `tool_error` with the reason if invalid) and pass `_adaptive_max_steps(task, override)` as
  the runner's `max_steps`. In the failure/error path, include the **stderr tail** from the runner
  result (see 3.6) instead of a generic message.
- [ ] **Step 3.5:** In `runner.py`, build the `Agent(...)` kwargs **conditionally from `PARAMS_0_12_6.md`**:
  for each of `use_vision='auto'`, `max_failures=<small int, e.g. 3>`, the timeout param (whichever name
  exists), `fallback_llm=<second available provider's LLM>`, `calculate_cost=True` — include it ONLY if
  Task 1 marked it present; pass `max_steps` to wherever Task 1 said it lives (`__init__` vs `Agent.run`).
  Build the `fallback_llm` from the next available key after the primary (Anthropic→OpenAI→Google).
- [ ] **Step 3.6:** In `runner.py`, capture browser-use's stderr/step log (it currently redirects it
  away) and include the **last ~2000 chars** in the emitted JSON (`{"ok":..., "result":..., "steps":...,
  "stderr_tail":...}`) so the parent can surface it on failure.
- [ ] **Step 3.7:** Run `pytest tests/test_browser_task_reliability.py -q` → PASS. `py_compile` both edited files.
- [ ] **Step 3.8: Commit**

```bash
git add src/voice-agent/browser_use_bridge/runner.py src/voice-agent/tools/browser.py src/voice-agent/tests/test_browser_task_reliability.py
git commit -m "feat(browser_task): adaptive steps + task validation + browser-use reliability params + legible failures"
```

---

### Task 4: Observability — surface the per-step trace into telemetry

**Files:** Modify `src/voice-agent/pipeline/turn_telemetry.py` (additive table), `src/voice-agent/tools/browser.py`.

- [ ] **Step 4.1:** In `turn_telemetry.py`, add an additive `CREATE TABLE IF NOT EXISTS browser_task_steps`
  (columns: `id INTEGER PK, ts_utc TEXT, task TEXT, step_index INTEGER, action TEXT, ok INTEGER,
  detail TEXT`) + a `record_browser_step(...)` helper mirroring the existing `computer_use_actions` write
  pattern. Additive only — do NOT alter the `turns` schema.
- [ ] **Step 4.2:** In `runner.py`, emit per-step `{step_index, action, ok, detail}` entries in the
  result JSON (`steps` already exists — enrich it to include action + ok per step).
- [ ] **Step 4.3:** In `tools/browser.py::_handle_browser_task`, after the runner returns, write each
  step via `record_browser_step(...)` (best-effort, failures silent — telemetry never breaks the tool).
- [ ] **Step 4.4:** Test: insert + read-back roundtrip for `browser_task_steps`
  (`tests/test_browser_task_reliability.py::test_step_table_roundtrip`, using a tmp DB). Run → PASS.
- [ ] **Step 4.5: Commit**

```bash
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/browser_use_bridge/runner.py src/voice-agent/tools/browser.py src/voice-agent/tests/test_browser_task_reliability.py
git commit -m "feat(browser_task): per-step trace into turn_telemetry.browser_task_steps (post-mortem debuggability)"
```

---

### Task 5: Verify (offline + live)

- [ ] **5.1** `py_compile` all edited python + bins; `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → full suite green.
- [ ] **5.2** Confirm `tools/browser.py` still imports with ONLY stdlib + registry (no browser_use import leak): `.venv/bin/python -c "import sys; sys.path.insert(0,'src/voice-agent'); import tools.browser"` clean.
- [ ] **5.3 Routing acceptance (the core P1 win), in BOTH supervisor and a direct mode:** three phrasings —
  "open YouTube on my screen" → must pick `computer_use`; "check the top Hacker News stories" → must pick
  `browser_task`; "find the RTX 6000 price on nvidia.com and tell me" → must pick `browser_task`. (Inspect
  the journal/telemetry tool-call for which tool fired.)
- [ ] **5.4 Live `browser_task` end-to-end:** "find the price of the RTX 6000 on nvidia.com and report back"
  → completes, returns a useful summary, and writes step rows to `browser_task_steps`; on an induced
  failure the stderr tail appears in the result.
- [ ] **5.5** End-of-task summary (CHANGED / NOT CHANGED / VERIFY).

**Acceptance:** the 3 routing phrasings route correctly in both modes; a real `browser_task` completes
with a telemetry step-trace and legible failures; full suite green; no schema-shape changes; no new deps.

---

## Self-review
- **Spec coverage:** P1.A → Task 2; P1.B → Tasks 1+3; P1.C → Task 4; verification → Task 5. All covered.
- **Placeholders:** the only deferred specifics (exact param names/shapes) are *intentionally* gated by
  Task 1's introspection — that's a real gate, not a placeholder. Routing text is fully written.
- **Consistency:** `_adaptive_max_steps` / `_validate_task` / `record_browser_step` names used
  consistently across Tasks 3-5.
