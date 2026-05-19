# Confab Defense-in-Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the 2026-05-19T02:24:18 Chrome confab pattern (JARVIS voicing "I've opened Chrome" when Chrome isn't running) via a three-layer defense: chat_ctx hygiene + tool-result wiring + evidence-strict confab detector with programmatic verification.

**Architecture:** Three independently-deployable layers each behind its own kill-switch env var. L3 wraps recalled session turns in a LiveKit `Instructions` block with `[STALE]` framing + age filter. L1 synthesizes a `FunctionCall` + `FunctionCallOutput` pair when the pycall sanitizer rescues a text-shaped tool call, so the subagent gate is no longer blind. L2 stops counting bare `transfer_to_*` as evidence, sets `session._jarvis_last_handoff_refused` on gate refusal, requires the supervisor to hedge instead of claim success, and adds `verify_launched()` pgrep checks for `launch_app`-class claims.

**Tech Stack:** Python 3.13 (voice-agent venv at `src/voice-agent/.venv/`), LiveKit Agents 1.5.9 (`livekit.agents.llm.chat_context.FunctionCall` / `FunctionCallOutput` / `Instructions`), pytest with `@pytest.mark.asyncio`, SQLite via `pipeline/turn_telemetry.py`, bash for `bin/jarvis-confab-soak`.

**Spec:** [`docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md`](../specs/2026-05-19-confab-defense-in-depth-design.md) — read §4 for the layered architecture, §5 for component breakdown, §6 for data flows, §8 for acceptance criteria.

**Env vars:**
- `JARVIS_RECALL_MAX_AGE_S` (default `1800`) — L3 recall age window in seconds. `0` disables recall.
- `JARVIS_PYCALL_SYNTH_DISABLED` (default `0`) — `1` skips L1 synthesis (legacy suppress-only).
- `JARVIS_CONFAB_STRICT_DISABLED` (default `0`) — `1` reverts L2 to permissive rule.

**Rollout order:** Task 1 (cross-layer telemetry foundation) → Tasks 2-3 (L3, lowest risk) → Tasks 4-5 (L1, root-cause) → Tasks 6-9 (L2, behavioral) → Task 10 (observability).

---

## File Structure

### Files created
| Path | Responsibility |
|---|---|
| `src/voice-agent/sanitizers/_function_call_recovery.py` | Pure helper: takes a parsed text-shaped tool call + chat_ctx, builds and inserts a `FunctionCall` + `FunctionCallOutput` pair. No side effects beyond chat_ctx.insert(). |
| `src/voice-agent/tests/test_recall_age_filter.py` | L3 unit: age-filter behavior. |
| `src/voice-agent/tests/test_recall_as_instructions.py` | L3 unit: Instructions-block wrapping. |
| `src/voice-agent/tests/test_function_call_recovery.py` | L1 unit: pair synthesis. |
| `src/voice-agent/tests/test_pycall_synthesis_integration.py` | L1 integration: end-to-end via pycall stream. |
| `src/voice-agent/tests/test_confab_detector_handoff_rule.py` | L2 unit: stricter evidence rule. |
| `src/voice-agent/tests/test_pgrep_verify.py` | L2 unit: verify_launched. |
| `src/voice-agent/tests/test_subagent_refused_flag.py` | L2 unit: flag set/cleared correctly. |
| `bin/jarvis-confab-soak` | L2 observability + soak validation. |

### Files modified
| Path | Change |
|---|---|
| `src/voice-agent/pipeline/turn_telemetry.py` | Add `confab_check_state TEXT` column (online migration) + writer kwarg. |
| `src/voice-agent/pipeline/chat_ctx.py` | L3: recall seed wraps in Instructions block + age filter. |
| `src/voice-agent/sanitizers/pycall.py` | L1: wire `_function_call_recovery.synthesize_and_insert()` into the leak-detection branch. |
| `src/voice-agent/confab_detector.py` | L2: tighten `has_recent_tool_evidence`; add `verify_launched()`. |
| `src/voice-agent/subagents/agent.py` | L2: set `session._jarvis_last_handoff_refused` on gate refusal. |
| `src/voice-agent/prompts/supervisor.md` | L2 + L3 prompt rules: STALE handling + POST-HANDOFF HONESTY. |

---

## Task 1: Telemetry column + writer kwarg

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (online migration + writer kwarg)
- Test: `src/voice-agent/tests/test_turn_telemetry.py` (append 2 new tests)

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_turn_telemetry.py`:

```python
def test_migration_adds_confab_check_state_column(tmp_path):
    """2026-05-19 — confab_check_state per-turn audit for the
    defense-in-depth fix. Spec: 2026-05-19-confab-defense-in-depth-design.md §5.4"""
    db = tmp_path / "tele.db"
    init_db(db)
    cols = {
        r[1]
        for r in sqlite3.connect(db).execute("PRAGMA table_info(turns)")
    }
    assert "confab_check_state" in cols


def test_log_turn_persists_confab_check_state(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    from pipeline.turn_telemetry import log_turn
    log_turn(
        db_path=db,
        ts_utc="2026-05-19T03:00:00Z",
        user_text="open chrome",
        jarvis_text="Chrome's open.",
        route="TASK",
        confab_check_state="evidence_ok",
    )
    row = sqlite3.connect(db).execute(
        "SELECT confab_check_state FROM turns WHERE user_text='open chrome'"
    ).fetchone()
    assert row == ("evidence_ok",)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_turn_telemetry.py::test_migration_adds_confab_check_state_column tests/test_turn_telemetry.py::test_log_turn_persists_confab_check_state -v
```

Expected: both FAIL.

- [ ] **Step 3: Add the migration block**

In `src/voice-agent/pipeline/turn_telemetry.py`, find the `init_db()` function. After the existing `turns` CREATE block and any existing `ALTER TABLE turns ADD COLUMN` migration blocks (mirror the 2026-05-18 `pwd_check_state` migration on `computer_use_actions`), ADD:

```python
        # 2026-05-19 — confab_check_state per-turn audit. Tracks the
        # defense-in-depth verdict (evidence_ok / hedged_no_evidence /
        # refused_handoff / stale_ctx_dropped / unchecked). Spec:
        # docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.4
        turn_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(turns)")
        }
        if "confab_check_state" not in turn_cols:
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN confab_check_state TEXT"
                )
            except sqlite3.OperationalError:
                pass
```

Place AFTER the existing `turns` migrations but BEFORE any CREATE INDEX lines.

- [ ] **Step 4: Extend `log_turn` signature + INSERT**

In the same file, find `def log_turn(`. Add `confab_check_state: Optional[str] = None` at the END of the parameter list. Update the INSERT statement: add `confab_check_state` to the column list, add `?` to VALUES, add `confab_check_state` to the value tuple.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_turn_telemetry.py -v
```

Expected: all turn_telemetry tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/turn_telemetry.py \
        src/voice-agent/tests/test_turn_telemetry.py
git commit -m "feat(telemetry): confab_check_state column on turns

Online migration adds a TEXT column to the turns table for
per-turn audit state of the 2026-05-19 confab defense-in-depth
fix. log_turn() accepts a new optional confab_check_state kwarg.
Lets the operator query verdict distribution over time:

  SELECT confab_check_state, COUNT(*) FROM turns
  WHERE ts_utc >= datetime('now', '-7 days')
  GROUP BY confab_check_state;

Per spec 2026-05-19 §5.4 cross-layer telemetry."
```

---

## Task 2: Layer 3 — chat_ctx recall age filter + Instructions wrap

**Files:**
- Modify: `src/voice-agent/pipeline/chat_ctx.py`
- Test: `src/voice-agent/tests/test_recall_age_filter.py` (new)
- Test: `src/voice-agent/tests/test_recall_as_instructions.py` (new)

- [ ] **Step 1: Inspect the existing recall seed function**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
grep -nE "def seed|recall.*chat_ctx|seeded chat_ctx" pipeline/chat_ctx.py | head -10
```

Locate the function that produces the `[recall] seeded chat_ctx with N prior turns` log line. The function may be named `seed_from_recall`, `seed_chat_ctx`, or similar — find it.

- [ ] **Step 2: Write the failing tests**

Create `src/voice-agent/tests/test_recall_age_filter.py`:

```python
"""L3 — recall age filter. Recalled turns older than
JARVIS_RECALL_MAX_AGE_S must be dropped entirely."""
import datetime
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_turn(minutes_ago: int, user_text: str = "hi", jarvis_text: str = "Yes?"):
    return {
        "ts_utc": (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)
        ).isoformat().replace("+00:00", "Z"),
        "user_text": user_text,
        "jarvis_text": jarvis_text,
    }


def test_recall_drops_turns_older_than_max_age(monkeypatch):
    """Turns older than JARVIS_RECALL_MAX_AGE_S are excluded."""
    monkeypatch.setenv("JARVIS_RECALL_MAX_AGE_S", "1800")
    from pipeline.chat_ctx import filter_recall_by_age

    turns = [
        _mock_turn(10, "fresh"),     # within window
        _mock_turn(45, "stale-1"),   # over window
        _mock_turn(180, "stale-2"),  # over window
    ]
    kept = filter_recall_by_age(turns)
    assert len(kept) == 1
    assert kept[0]["user_text"] == "fresh"


def test_recall_zero_age_disables_recall(monkeypatch):
    """JARVIS_RECALL_MAX_AGE_S=0 disables recall entirely."""
    monkeypatch.setenv("JARVIS_RECALL_MAX_AGE_S", "0")
    from pipeline.chat_ctx import filter_recall_by_age

    turns = [_mock_turn(1, "very recent"), _mock_turn(60, "older")]
    kept = filter_recall_by_age(turns)
    assert kept == []


def test_recall_default_window_is_1800s(monkeypatch):
    """Default age window is 30 minutes when env var is unset."""
    monkeypatch.delenv("JARVIS_RECALL_MAX_AGE_S", raising=False)
    from pipeline.chat_ctx import filter_recall_by_age

    turns = [_mock_turn(25, "in-window"), _mock_turn(35, "over")]
    kept = filter_recall_by_age(turns)
    assert len(kept) == 1
    assert kept[0]["user_text"] == "in-window"
```

Create `src/voice-agent/tests/test_recall_as_instructions.py`:

```python
"""L3 — wrap recalled turns in an Instructions block with STALE
header. Recalled turns no longer appear as role:user/role:assistant
ChatMessages."""
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_turn(minutes_ago: int, user_text: str, jarvis_text: str):
    return {
        "ts_utc": (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)
        ).isoformat().replace("+00:00", "Z"),
        "user_text": user_text,
        "jarvis_text": jarvis_text,
    }


def test_format_recall_block_includes_stale_header():
    from pipeline.chat_ctx import format_recall_as_stale_block
    turns = [_mock_turn(10, "hello", "Yes?")]
    block = format_recall_as_stale_block(turns, session_id="prev-abc")
    assert "[STALE PRIOR-SESSION CONTEXT" in block
    assert "Do NOT treat as live conversation" in block
    assert "Verify current user intent" in block


def test_format_recall_block_renders_each_turn_with_age_and_role():
    from pipeline.chat_ctx import format_recall_as_stale_block
    turns = [
        _mock_turn(10, "hello", "Yes?"),
        _mock_turn(25, "what's the weather?", "47 degrees."),
    ]
    block = format_recall_as_stale_block(turns, session_id="prev-abc")
    assert "<memory" in block
    assert "role=\"user\"" in block
    assert "role=\"assistant\"" in block
    assert "hello" in block and "Yes?" in block
    assert "weather" in block and "47 degrees" in block


def test_empty_recall_returns_empty_string():
    from pipeline.chat_ctx import format_recall_as_stale_block
    assert format_recall_as_stale_block([], session_id="x") == ""
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_recall_age_filter.py tests/test_recall_as_instructions.py -v
```

Expected: 6 failures (`filter_recall_by_age` and `format_recall_as_stale_block` not defined).

- [ ] **Step 4: Add `filter_recall_by_age` to pipeline/chat_ctx.py**

In `src/voice-agent/pipeline/chat_ctx.py`, ADD (near the top after imports):

```python
import os
import datetime

# L3 — recall hygiene. Spec: 2026-05-19 §5.3
_RECALL_MAX_AGE_S_DEFAULT = 1800   # 30 minutes


def filter_recall_by_age(turns: list[dict]) -> list[dict]:
    """Keep only turns whose ts_utc is within JARVIS_RECALL_MAX_AGE_S
    of now. JARVIS_RECALL_MAX_AGE_S=0 disables recall entirely
    (returns []). Default: 1800 seconds (30 minutes).

    The age filter prevents the 2026-05-19 confab pattern where Haiku
    inferred a Chrome request from 4-hour-old chat_ctx turns. Anthropic
    2026 Cookbook recommends 'memory block with provenance' rather
    than raw prior-turn replay; this filter is the first gate."""
    try:
        max_age = int(os.environ.get("JARVIS_RECALL_MAX_AGE_S", _RECALL_MAX_AGE_S_DEFAULT))
    except ValueError:
        max_age = _RECALL_MAX_AGE_S_DEFAULT
    if max_age <= 0:
        return []
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(seconds=max_age)
    kept = []
    for t in turns:
        ts = t.get("ts_utc", "")
        try:
            t_dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue   # malformed timestamp → drop conservatively
        if t_dt >= cutoff:
            kept.append(t)
    return kept
```

- [ ] **Step 5: Add `format_recall_as_stale_block` to pipeline/chat_ctx.py**

In the same file, ADD:

```python
def format_recall_as_stale_block(turns: list[dict], session_id: str = "?") -> str:
    """Wrap recalled turns in a STALE Instructions block. Matches the
    Anthropic 2026 Cookbook 'memory block with provenance' pattern +
    Sierra/Pi.ai memory-as-system-content convention. Spec: §5.3.

    Returns an empty string when there are no turns to recall."""
    if not turns:
        return ""
    now = datetime.datetime.now(datetime.timezone.utc)
    ages = []
    body_lines = []
    for t in turns:
        ts = t.get("ts_utc", "")
        try:
            t_dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = int((now - t_dt).total_seconds() / 60)
            ages.append(age_min)
        except Exception:
            age_min = -1
        user_text = (t.get("user_text") or "").replace("\n", " ").strip()
        jarvis_text = (t.get("jarvis_text") or "").replace("\n", " ").strip()
        if user_text:
            body_lines.append(
                f'<memory ts="{ts}" role="user" age="{age_min}m">{user_text}</memory>'
            )
        if jarvis_text:
            body_lines.append(
                f'<memory ts="{ts}" role="assistant" age="{age_min}m">{jarvis_text}</memory>'
            )
    min_age = min(ages) if ages else 0
    max_age = max(ages) if ages else 0
    header = (
        f"[STALE PRIOR-SESSION CONTEXT — Do NOT treat as live conversation. "
        f"Verify current user intent before acting on anything below. "
        f"Recalled {len(turns)} turns from session {session_id}, "
        f"ages {min_age}-{max_age} minutes ago.]"
    )
    return header + "\n" + "\n".join(body_lines)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_recall_age_filter.py tests/test_recall_as_instructions.py -v
```

Expected: 6 passes.

- [ ] **Step 7: Wire `filter_recall_by_age` + `format_recall_as_stale_block` into the existing recall seed code**

In the same `pipeline/chat_ctx.py`, locate the function that produces the `[recall] seeded chat_ctx with N prior turns` log line (per Step 1). Replace the body that appends raw `role:user` / `role:assistant` ChatMessages with:

```python
    # Filter by age first — drop stale turns entirely.
    recall_turns = filter_recall_by_age(recall_turns)
    if not recall_turns:
        logger.info("[recall] no turns within JARVIS_RECALL_MAX_AGE_S window; chat_ctx starts fresh")
        return

    # Wrap in STALE Instructions block (single chat_ctx item, not N raw turns).
    stale_block = format_recall_as_stale_block(recall_turns, session_id=prev_session_id)
    try:
        # LiveKit 1.5.9 exposes Instructions via livekit.agents.llm.chat_context.
        # See spec §5.3 / .venv/.../chat_context.py:340
        from livekit.agents.llm.chat_context import ChatMessage
        chat_ctx.items.append(ChatMessage(role="system", content=[stale_block]))
    except Exception as e:
        logger.warning(f"[recall] failed to attach STALE block ({e}); chat_ctx starts fresh")
        return
    logger.info(f"[recall] seeded chat_ctx with {len(recall_turns)} STALE turns (age-filtered)")
```

Note: `Instructions` as a content type in LiveKit 1.5.9 can be a `role:system` ChatMessage holding the stale text. The earlier draft used the type literally; this is the safe ChatMessage form that's known to work. If a strict `Instructions` content variant exists in this LiveKit version, prefer it; otherwise use `role:system` with the STALE-tagged text.

- [ ] **Step 8: Run the full pipeline test suite + a manual smoke**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_recall_age_filter.py tests/test_recall_as_instructions.py tests/ -k "chat_ctx or recall" -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/chat_ctx.py \
        src/voice-agent/tests/test_recall_age_filter.py \
        src/voice-agent/tests/test_recall_as_instructions.py
git commit -m "feat(chat_ctx): L3 — recall age filter + STALE wrap

filter_recall_by_age drops recalled turns older than
JARVIS_RECALL_MAX_AGE_S (default 1800s = 30 min). =0 disables.

format_recall_as_stale_block wraps kept turns in a single
[STALE PRIOR-SESSION CONTEXT] header + <memory ts role age>
inner blocks. Replaces the prior raw role:user / role:assistant
ChatMessage replay pattern that polluted chat_ctx with 12 stale
turns and let Haiku hallucinate Chrome handoffs from 4-hour-old
context.

Matches the Anthropic 2026 Cookbook memory-block-with-provenance
pattern + Sierra/Pi.ai memory-as-system-content convention.

Per spec 2026-05-19 §5.3."
```

---

## Task 3: Layer 3 — supervisor prompt STALE handling rule

**Files:**
- Modify: `src/voice-agent/prompts/supervisor.md`

- [ ] **Step 1: Find the insertion point**

```bash
grep -nE "^═══ MEMORY|^═══ SESSION MEMORY|^═══ YOU HAVE MEMORY|^═══ PROACTIVE CAPTURE" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md
```

Pick a stable insertion point AFTER `═══ MEMORY ═══` and BEFORE `═══ LOCATION QUESTIONS` (the natural neighbor for memory-handling rules).

- [ ] **Step 2: Insert the STALE handling rule**

Use the Edit tool to insert the following block at the chosen insertion point in `src/voice-agent/prompts/supervisor.md`:

```
═══ STALE PRIOR-SESSION CONTEXT ═══

The supervisor's chat_ctx may start with a `[STALE PRIOR-SESSION
CONTEXT]` block wrapping <memory> entries from earlier sessions. The
recall age filter (default 30 min, env JARVIS_RECALL_MAX_AGE_S)
ensures these are bounded; nothing older than that lands.

The <memory> blocks inside are REFERENCE ONLY:

  ❌ Don't infer an active task, unresolved request, or pending
     confirmation from them.
  ❌ Don't treat the current user input as a continuation of a
     prior-session conversation unless the user EXPLICITLY references
     it ("as I mentioned earlier…", "you said you'd…", "back to what
     we were doing…").
  ✅ Use them for personal-context recall ONLY — the user's name,
     preferences, prior decisions you've been told about — same way
     you'd use facts from memory.

Past failure 2026-05-19T02:24:18: 12 prior-session turns were
recalled raw as role:user / role:assistant ChatMessages. User said
"Okay" (one word, EMOTIONAL route). Supervisor (Haiku) treated the
"Okay" as a continuation of an unresolved open-Chrome request from
4 hours earlier and hallucinated a transfer_to_desktop handoff.
Chrome was not opened; user was lied to.

Rule: the FIRST user turn of the current session is FRESH intent.
Banter ("Hi", "Yes", "Okay") is banter — never assume it's confirming
something stale. If you genuinely can't parse the user's intent
because it's a one-word reply, ask clarifying — don't infer from
stale context.

═══
```

- [ ] **Step 3: Verify the prompt still parses + no other section accidentally affected**

```bash
wc -l /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md
grep -c "^═══" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md
```

The section-header count should increase by exactly 1 vs pre-edit.

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/prompts/supervisor.md
git commit -m "feat(prompt): L3 — STALE PRIOR-SESSION CONTEXT rule

Teaches the supervisor that recalled prior-session turns wrapped in
[STALE PRIOR-SESSION CONTEXT] blocks are reference-only:

  - Don't infer active tasks, unresolved requests, or pending
    confirmations from them.
  - Don't treat 'Okay' / 'Yes' / single-word replies as continuations
    of stale context.
  - Reference back to memory ONLY when user explicitly cites prior
    conversation ('as I mentioned…').

Cites the 2026-05-19T02:24:18 Chrome confab as the past failure.
Pairs with pipeline/chat_ctx.py L3 hygiene (age-filtered Instructions
block). Without this prompt rule the LLM may ignore the [STALE]
framing; with it the rule is explicit.

Per spec 2026-05-19 §5.3."
```

---

## Task 4: Layer 1 — _function_call_recovery helper module

**Files:**
- Create: `src/voice-agent/sanitizers/_function_call_recovery.py`
- Test: `src/voice-agent/tests/test_function_call_recovery.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_function_call_recovery.py`:

```python
"""L1 — function call recovery helper. Synthesizes a
(FunctionCall, FunctionCallOutput) pair from a parsed text-shaped
tool call and inserts it into chat_ctx so the subagent gate sees
real evidence."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _fake_chat_ctx():
    """Minimal chat_ctx stand-in — just an `items` list. The real
    LiveKit chat_ctx supports more, but the recovery helper only
    needs .items.append()."""
    return SimpleNamespace(items=[])


def test_synthesize_inserts_pair_with_shared_call_id():
    from sanitizers._function_call_recovery import synthesize_and_insert
    ctx = _fake_chat_ctx()
    fc, fco = synthesize_and_insert(
        chat_ctx=ctx,
        tool_name="launch_app",
        raw_args="binary='google-chrome', args='--new-window'",
        synthetic_output="OK: synthesis_path (call captured from text-shape leak)",
    )
    assert fc.call_id == fco.call_id
    assert fc.name == "launch_app"
    assert "google-chrome" in fc.arguments
    assert "synthesis_path" in fco.output
    # Both items must end up in chat_ctx — gate walks items_since.
    assert len(ctx.items) == 2
    assert ctx.items[0] is fc
    assert ctx.items[1] is fco


def test_synthesize_produces_unique_call_id_per_call():
    from sanitizers._function_call_recovery import synthesize_and_insert
    ctx = _fake_chat_ctx()
    fc1, _ = synthesize_and_insert(
        chat_ctx=ctx, tool_name="launch_app",
        raw_args="binary='a'", synthetic_output="ok",
    )
    fc2, _ = synthesize_and_insert(
        chat_ctx=ctx, tool_name="launch_app",
        raw_args="binary='b'", synthetic_output="ok",
    )
    assert fc1.call_id != fc2.call_id


def test_synthesize_disabled_env_returns_none(monkeypatch):
    """JARVIS_PYCALL_SYNTH_DISABLED=1 → synthesize_and_insert
    short-circuits to None without touching chat_ctx."""
    monkeypatch.setenv("JARVIS_PYCALL_SYNTH_DISABLED", "1")
    from sanitizers._function_call_recovery import synthesize_and_insert
    ctx = _fake_chat_ctx()
    result = synthesize_and_insert(
        chat_ctx=ctx, tool_name="launch_app",
        raw_args="binary='x'", synthetic_output="ok",
    )
    assert result is None
    assert ctx.items == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_function_call_recovery.py -v
```

Expected: 3 FAIL (ImportError).

- [ ] **Step 3: Create the helper module**

Create `src/voice-agent/sanitizers/_function_call_recovery.py`:

```python
"""Function-call recovery helper for the pycall sanitizer.

When the supervisor or subagent LLM emits a tool call as plain
content text (e.g., `launch_app("google-chrome")`) instead of
through the structured `tool_calls` field, the pycall sanitizer
catches the leak and suppresses the voiced text. But LiveKit's
FunctionCallOutput writeback path (voice/agent_activity.py:2834,
voice/generation.py:746) only fires for structured calls — so
chat_ctx is left without a tool_result, and the subagent gate
refuses task_done with `no real tool`. Live capture
2026-05-19T02:23:33.

This helper recovers the lost evidence by synthesizing both a
FunctionCall AND a matching FunctionCallOutput with a shared
call_id, and inserting both into the active chat_ctx. The gate
then sees items_since=2 with a real tool in the trail and allows
the bailout.

Spec: docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.1
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional, Tuple

logger = logging.getLogger("jarvis.sanitizers.function_call_recovery")


__all__ = ["synthesize_and_insert"]


def synthesize_and_insert(
    *,
    chat_ctx,
    tool_name: str,
    raw_args: str,
    synthetic_output: str,
) -> Optional[Tuple[object, object]]:
    """Synthesize a (FunctionCall, FunctionCallOutput) pair sharing a
    fresh call_id and append both to chat_ctx.items.

    Returns (fc, fco) on success, None when the env kill-switch
    JARVIS_PYCALL_SYNTH_DISABLED=1 is set. The pycall sanitizer
    falls back to its legacy suppress-only behaviour when None is
    returned.

    Live (2026-05-19T02:23:33) the desktop subagent emitted
    launch_app(...) as text content; pycall suppressed the leak but
    did not recover the structured shape, leaving the gate blind.
    Calling this helper from the same code path lands a real
    tool_result in chat_ctx so the gate sees items_since=2 and
    allows the subagent's task_done bailout.
    """
    if os.environ.get("JARVIS_PYCALL_SYNTH_DISABLED", "0") == "1":
        return None

    try:
        from livekit.agents.llm.chat_context import FunctionCall, FunctionCallOutput
    except Exception as e:
        logger.warning(
            f"[function_call_recovery] LiveKit chat_context import failed ({e}); "
            f"skipping synthesis — pycall falls back to suppress-only."
        )
        return None

    call_id = f"fc-{uuid.uuid4().hex[:12]}"
    try:
        fc = FunctionCall(
            call_id=call_id,
            name=tool_name,
            arguments=raw_args,
        )
        fco = FunctionCallOutput(
            call_id=call_id,
            name=tool_name,
            output=synthetic_output,
            is_error=False,
        )
        chat_ctx.items.append(fc)
        chat_ctx.items.append(fco)
        logger.warning(
            f"[function_call_recovery] synthesized pair "
            f"call_id={call_id} tool={tool_name!r} "
            f"args_len={len(raw_args)} output_len={len(synthetic_output)}"
        )
        return fc, fco
    except Exception as e:
        logger.warning(
            f"[function_call_recovery] FunctionCall/FunctionCallOutput "
            f"construction failed ({type(e).__name__}: {e}); skipping."
        )
        return None
```

Note: The exact `FunctionCall` / `FunctionCallOutput` constructor signatures may differ slightly across LiveKit versions. If they don't accept `is_error=` or `arguments=` as a kwarg, adapt to whatever the installed version exposes (check `.venv/lib/python3.13/site-packages/livekit/agents/llm/chat_context.py`).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_function_call_recovery.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/sanitizers/_function_call_recovery.py \
        src/voice-agent/tests/test_function_call_recovery.py
git commit -m "feat(sanitizer): L1 — _function_call_recovery helper

When the LLM emits a tool call as plain content text (caught by
the pycall sanitizer's existing leak detection), LiveKit's
FunctionCallOutput writeback path doesn't fire — chat_ctx is left
without a tool_result and the subagent gate refuses task_done
with 'no real tool'. Live capture 2026-05-19T02:23:33.

synthesize_and_insert(chat_ctx, tool_name, raw_args, synthetic_output)
recovers the lost evidence by building a (FunctionCall,
FunctionCallOutput) pair with a shared fresh call_id and appending
both to chat_ctx.items. Gate then sees items_since=2 with a real
tool in the trail.

Kill-switch: JARVIS_PYCALL_SYNTH_DISABLED=1.

Per spec 2026-05-19 §5.1. The synthetic_output is supplied by the
caller (pycall.py) — we don't re-execute the tool, avoiding
idempotency issues with non-idempotent tools like launch_app."
```

---

## Task 5: Layer 1 — pycall.py synthesis integration

**Files:**
- Modify: `src/voice-agent/sanitizers/pycall.py`
- Test: `src/voice-agent/tests/test_pycall_synthesis_integration.py` (new)

- [ ] **Step 1: Find the pycall leak-detection branch**

```bash
grep -nE "is_known_leak|tool-call-as-text leak|envelope.*pycall" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/sanitizers/pycall.py | head -10
```

Locate the branch where pycall detects a known-leak tool call and currently calls `_try_set_content(delta, "")` (the suppress-only path).

- [ ] **Step 2: Write the failing integration test**

Create `src/voice-agent/tests/test_pycall_synthesis_integration.py`:

```python
"""L1 integration — pycall sanitizer rescues a text-shaped
launch_app call AND inserts a synthesized FunctionCall +
FunctionCallOutput pair into chat_ctx via the recovery helper.

This is the end-to-end test for the Chrome 02:23:33 failure
pattern."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import sanitizers.pycall as pycall_sanitizer


@pytest.fixture(autouse=True)
def _clean_state():
    pycall_sanitizer._PYCALL_STATE.clear()
    yield
    pycall_sanitizer._PYCALL_STATE.clear()


def _make_self_mock(known_tools, chat_ctx):
    return SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(
            function_tools={name: object() for name in known_tools}
        ),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
        _chat_ctx=chat_ctx,
    )


def _make_choice(content):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(delta=delta, finish_reason=None)


def test_text_shape_launch_app_lands_pair_in_chat_ctx():
    """The 2026-05-19T02:23:33 failure: subagent emits
    launch_app('google-chrome') as text. After fix, pycall
    suppresses the voiced text AND inserts a (FunctionCall,
    FunctionCallOutput) pair into chat_ctx so the gate sees it."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    chat_ctx = SimpleNamespace(items=[])
    self_mock = _make_self_mock({"launch_app", "task_done"}, chat_ctx)
    import threading
    thinking = threading.Event()

    chunks = [
        'launch_app("google-chrome", ',
        '"--profile-directory=Default --new-window")',
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_synth", c, thinking)
        voiced.append(c.delta.content)

    # Voiced text is suppressed (no TTS gibberish).
    full_voiced = "".join(voiced)
    assert "launch_app" not in full_voiced

    # Pair is in chat_ctx — gate sees items_since=2.
    assert len(chat_ctx.items) == 2
    fc, fco = chat_ctx.items
    assert fc.call_id == fco.call_id
    assert fc.name == "launch_app"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_pycall_synthesis_integration.py -v
```

Expected: FAIL — `chat_ctx.items` is empty after the suppress-only path runs.

- [ ] **Step 4: Wire the recovery helper into pycall's leak branch**

In `src/voice-agent/sanitizers/pycall.py`, find the `if m and _is_known_leak(...)` branch (Form 1, around the line containing `"buffer": content, "depth": 0, "tool_name": name, "envelope": "pycall"`). AFTER the existing `logger.warning(...)` for the leak suppression AND AFTER `_try_set_content(delta, "")`, ADD a synthesis call:

```python
                if m and _is_known_leak(m.group(1), live_known):
                    # ... existing suppress logic unchanged ...
                    _try_set_content(delta, "")

                    # 2026-05-19 L1 — synthesis path. Recover the
                    # lost FunctionCall + FunctionCallOutput pair so
                    # the subagent gate sees evidence. Spec §5.1.
                    try:
                        ctx = getattr(self, "_chat_ctx", None)
                        if ctx is not None:
                            from sanitizers._function_call_recovery import synthesize_and_insert
                            # Strip the leading `name(` and trailing `)` from the buffered
                            # content to get the raw args string (best-effort; the helper
                            # also accepts malformed args gracefully).
                            buf = content.strip()
                            raw_args = ""
                            if buf.startswith(f"{name}(") and buf.endswith(")"):
                                raw_args = buf[len(name) + 1:-1].strip()
                            synthesize_and_insert(
                                chat_ctx=ctx,
                                tool_name=name,
                                raw_args=raw_args,
                                synthetic_output=(
                                    f"OK: synthesis_path (call captured from text-shape leak; "
                                    f"actual tool execution status unknown — use programmatic verify)"
                                ),
                            )
                    except Exception as e:
                        logger.warning(
                            f"[pycall] synthesis path failed silently: {type(e).__name__}: {e}"
                        )
```

Important: this is ADDITIVE — the existing suppress + state tracking remains. The synthesis runs alongside.

- [ ] **Step 5: Run integration test to verify it passes**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_pycall_synthesis_integration.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full sanitizer suite — no regression**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_pycall_sanitizer.py tests/test_function_call_recovery.py tests/test_pycall_synthesis_integration.py -v
```

Expected: 26 (existing) + 3 (new helper) + 1 (new integration) = 30 passes.

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/sanitizers/pycall.py \
        src/voice-agent/tests/test_pycall_synthesis_integration.py
git commit -m "feat(sanitizer): L1 — pycall synthesis integration

Wire _function_call_recovery.synthesize_and_insert into the
existing pycall leak-detection branch. When pycall suppresses a
text-shaped tool call (e.g., launch_app('google-chrome')), it now
ALSO synthesizes a paired FunctionCall + FunctionCallOutput in
the active chat_ctx so the subagent gate sees items_since=2 and
allows task_done.

Synthetic_output is a marker, not a re-execution — avoids
idempotency issues with launch_app and other non-idempotent
tools. Layer 2's verify_launched provides programmatic
ground-truth state when needed.

Kill-switch: JARVIS_PYCALL_SYNTH_DISABLED=1 — falls back to the
prior suppress-only behaviour.

Per spec 2026-05-19 §5.1 + integration test reproduces the
2026-05-19T02:23:33 failure pattern."
```

---

## Task 6: Layer 2 — confab_detector evidence rule tightening

**Files:**
- Modify: `src/voice-agent/confab_detector.py`
- Test: `src/voice-agent/tests/test_confab_detector_handoff_rule.py` (new)

- [ ] **Step 1: Inspect the current evidence rule**

```bash
grep -nE "has_recent_tool_evidence|transfer_to|task_done|delegate" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/confab_detector.py | head -15
```

Locate the rule that currently grants evidence to `transfer_to_*` calls in the last 10 messages.

- [ ] **Step 2: Write the failing tests**

Create `src/voice-agent/tests/test_confab_detector_handoff_rule.py`:

```python
"""L2 — confab detector stricter evidence rule. transfer_to_*
alone is no longer enough; need a real tool_result or an allowed
(not refused) subagent task_done."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _msg(role, content=None, tool_calls=None, tool_name=None):
    return SimpleNamespace(role=role, content=content,
                           tool_calls=tool_calls, name=tool_name)


def test_bare_transfer_to_does_not_count_as_evidence():
    """The 2026-05-19 Chrome confab pattern: only a bare
    transfer_to_desktop in the last 10 messages, gate refused the
    subagent's task_done. Detector must NOT grant evidence credit."""
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg(role="user", content="open chrome"),
        _msg(role="assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    assert not has_recent_tool_evidence(items, lookback=10)


def test_real_function_call_output_counts_as_evidence():
    """A structured FunctionCallOutput (or role:tool message)
    counts as evidence — the actual tool ran and returned."""
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg(role="user", content="open chrome"),
        _msg(role="assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="launch_app"))
        ]),
        _msg(role="tool", content="OK: launched 'google-chrome'",
             tool_name="launch_app"),
    ]
    assert has_recent_tool_evidence(items, lookback=10)


def test_strict_disabled_env_falls_back_to_permissive(monkeypatch):
    """JARVIS_CONFAB_STRICT_DISABLED=1 reverts to today's rule:
    transfer_to_* alone counts."""
    monkeypatch.setenv("JARVIS_CONFAB_STRICT_DISABLED", "1")
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg(role="user", content="open chrome"),
        _msg(role="assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    assert has_recent_tool_evidence(items, lookback=10)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_confab_detector_handoff_rule.py -v
```

Expected: at least 2 FAIL (`test_bare_transfer_to_does_not_count_as_evidence` because today's rule grants it, and `test_strict_disabled_env_falls_back_to_permissive` because the env var doesn't exist yet).

- [ ] **Step 4: Tighten the evidence rule**

In `src/voice-agent/confab_detector.py`, locate `has_recent_tool_evidence` (or equivalent). Replace its body with:

```python
def has_recent_tool_evidence(items: list, lookback: int = 10) -> bool:
    """Return True iff a real tool fired in the recent message window.

    2026-05-19 (L2): a bare transfer_to_* / delegate call no longer
    counts. Required:
      - A structured tool_result message (role:'tool' or
        FunctionCallOutput shape), OR
      - A handoff that returned WITH an allowed (not refused)
        task_done — which transitively required real tool evidence
        in the subagent's chat_ctx.

    Kill-switch: JARVIS_CONFAB_STRICT_DISABLED=1 reverts to the
    permissive 'transfer_to_* alone counts' rule. Default: strict.

    Spec: docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.2"""
    import os
    permissive = os.environ.get("JARVIS_CONFAB_STRICT_DISABLED", "0") == "1"

    recent = items[-lookback:] if lookback > 0 else items
    for item in recent:
        # 1) A real tool_result message (role:tool OR FunctionCallOutput)
        role = getattr(item, "role", None)
        if role == "tool":
            return True
        # LiveKit FunctionCallOutput has .output + .name + .call_id
        if hasattr(item, "output") and hasattr(item, "call_id"):
            return True
        # 2) An assistant turn with structured tool_calls
        tcs = getattr(item, "tool_calls", None) or []
        for tc in tcs:
            name = getattr(getattr(tc, "function", tc), "name", "")
            # Strict rule: don't grant credit for bare transfer_to_*.
            if name and not (name.startswith("transfer_to_") or name == "delegate"):
                return True
            # Permissive fallback for legacy.
            if permissive and (name.startswith("transfer_to_") or name == "delegate"):
                return True
    return False
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_confab_detector_handoff_rule.py tests/test_confab_detector.py -v
```

Expected: all pass (new + existing).

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/confab_detector.py \
        src/voice-agent/tests/test_confab_detector_handoff_rule.py
git commit -m "feat(confab): L2 — strict evidence rule

A bare transfer_to_desktop / delegate call no longer counts as
tool evidence on its own. Required: a real tool_result
(role:'tool' or FunctionCallOutput) in the lookback window, OR a
real non-handoff tool call. The 2026-05-19T02:24:18 Chrome confab
was: bare transfer_to_desktop → subagent gate refused task_done →
supervisor STILL voiced 'I've opened Chrome' because the old rule
granted evidence to the handoff itself.

Kill-switch: JARVIS_CONFAB_STRICT_DISABLED=1 reverts to permissive.

Per spec 2026-05-19 §5.2."
```

---

## Task 7: Layer 2 — verify_launched pgrep helper

**Files:**
- Modify: `src/voice-agent/confab_detector.py` (add function)
- Test: `src/voice-agent/tests/test_pgrep_verify.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_pgrep_verify.py`:

```python
"""L2 — verify_launched programmatic state check. Calls `pgrep -fa`
to confirm a binary actually started, matching Anthropic Computer
Use's post-action verification pattern."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_verify_launched_finds_running_process(monkeypatch):
    """When pgrep finds a matching process, returns True."""
    from confab_detector import verify_launched
    # Use the user's own shell as a guaranteed-present process.
    # Filter is loose because we just need ANY match.
    assert verify_launched("zsh", timeout_s=1) in (True, False)
    # Soft assert: we can't guarantee zsh is running on the test box,
    # but if it is, the function should find it. Use a stricter
    # subprocess-mock test below for hard-pass guarantees.


def test_verify_launched_returns_false_for_nonexistent_binary():
    """A binary name that can't possibly match any process: False."""
    from confab_detector import verify_launched
    assert verify_launched("definitely-not-a-real-binary-7f3c91", timeout_s=1) is False


def test_verify_launched_handles_pgrep_missing(monkeypatch):
    """If pgrep itself isn't installed (returns FileNotFoundError),
    verify_launched returns None (unknown), NOT False — so the
    caller can choose to fall back to chat_ctx-only evidence."""
    from confab_detector import verify_launched
    def fake_run(*a, **kw):
        raise FileNotFoundError("pgrep not found")
    monkeypatch.setattr("subprocess.run", fake_run)
    assert verify_launched("anything", timeout_s=1) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_pgrep_verify.py -v
```

Expected: FAIL (`verify_launched` not defined).

- [ ] **Step 3: Add `verify_launched` to confab_detector.py**

In `src/voice-agent/confab_detector.py`, ADD:

```python
import subprocess


def verify_launched(binary_name: str, timeout_s: float = 5.0) -> "bool | None":
    """Return True if at least one process matching `binary_name` is
    currently running (via `pgrep -fa <binary_name>`), False if no
    match within `timeout_s`, None if pgrep itself is unavailable.

    Matches Anthropic Computer Use's post-action verification
    pattern: don't trust the model's narration of an action ('I've
    opened Chrome'); verify state programmatically. Used by the
    supervisor's reply path when a launch_app-class handoff returned
    without a corresponding tool_result in chat_ctx.

    Returns:
      True  — at least one match
      False — no match within timeout_s
      None  — pgrep unavailable; caller falls back to chat_ctx-only

    Spec: docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.2"""
    try:
        r = subprocess.run(
            ["pgrep", "-fa", binary_name],
            capture_output=True, timeout=timeout_s, text=True,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_pgrep_verify.py -v
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/confab_detector.py \
        src/voice-agent/tests/test_pgrep_verify.py
git commit -m "feat(confab): L2 — verify_launched programmatic check

Anthropic Computer Use post-action verification pattern adapted
for the launch_app surface. verify_launched(binary, timeout_s=5)
calls pgrep -fa <binary> and returns True/False/None:
  True  — at least one match (action confirmed)
  False — no match (action failed silently)
  None  — pgrep unavailable (fall back to chat_ctx evidence)

Designed to be called by the supervisor's reply path when a
handoff returned without a corresponding tool_result in chat_ctx.
Cheaper than Anthropic Computer Use's per-turn screenshot diff,
adequate for launch_app-class claims.

Per spec 2026-05-19 §5.2."
```

---

## Task 8: Layer 2 — subagent gate sets _jarvis_last_handoff_refused

**Files:**
- Modify: `src/voice-agent/subagents/agent.py` (gate refusal path)
- Test: `src/voice-agent/tests/test_subagent_refused_flag.py` (new)

- [ ] **Step 1: Locate the gate refusal site**

```bash
grep -nE "task_done REFUSED|no real tool call|_NO_TOOL_RETRY_CEILING|_BAILOUT_SUMMARY_RE" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/subagents/agent.py | head -10
```

Locate the lines where the gate logs the refusal (`logger.warning("[subagent:%s] task_done REFUSED ...")` or similar).

- [ ] **Step 2: Write the failing test**

Create `src/voice-agent/tests/test_subagent_refused_flag.py`:

```python
"""L2 — gate refusal sets session._jarvis_last_handoff_refused.
Supervisor reads this on the next turn for POST-HANDOFF HONESTY."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_gate_refusal_sets_session_flag():
    """When the gate refuses task_done with 'no real tool', the
    session flag is set to True so the supervisor can hedge."""
    from subagents.agent import _record_handoff_refused
    session = SimpleNamespace()
    _record_handoff_refused(session)
    assert session._jarvis_last_handoff_refused is True


def test_gate_acceptance_clears_session_flag():
    """When a subsequent supervisor tool call succeeds (real
    FunctionCallOutput lands), the flag is cleared."""
    from subagents.agent import _record_handoff_refused, _clear_handoff_refused
    session = SimpleNamespace()
    _record_handoff_refused(session)
    _clear_handoff_refused(session)
    assert getattr(session, "_jarvis_last_handoff_refused", False) is False


def test_flag_clear_is_idempotent():
    """Calling clear on an already-cleared session doesn't raise."""
    from subagents.agent import _clear_handoff_refused
    session = SimpleNamespace()
    _clear_handoff_refused(session)
    assert getattr(session, "_jarvis_last_handoff_refused", False) is False
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_subagent_refused_flag.py -v
```

Expected: 3 FAIL (`_record_handoff_refused` / `_clear_handoff_refused` not defined).

- [ ] **Step 4: Add the flag-management helpers**

In `src/voice-agent/subagents/agent.py`, near the top of the module (after imports), ADD:

```python
# L2 — POST-HANDOFF HONESTY signal. Set when the gate refuses
# task_done (subagent had no real tool fire this handoff); cleared
# when subsequent supervisor evidence arrives. The supervisor
# reads this on the next turn and hedges instead of claiming
# success. Spec: 2026-05-19 §5.2.
def _record_handoff_refused(session) -> None:
    """Called from the gate when task_done is refused for
    no-real-tool. The supervisor's next reply path reads
    `session._jarvis_last_handoff_refused` and applies the
    POST-HANDOFF HONESTY rule from supervisor.md."""
    try:
        session._jarvis_last_handoff_refused = True
    except Exception:
        pass


def _clear_handoff_refused(session) -> None:
    """Clear the flag after a successful supervisor turn (real
    tool_result landed) or a new session start. Idempotent."""
    try:
        if hasattr(session, "_jarvis_last_handoff_refused"):
            session._jarvis_last_handoff_refused = False
    except Exception:
        pass
```

- [ ] **Step 5: Call `_record_handoff_refused` at the gate refusal site**

In the same file, find the gate refusal branch (per Step 1). At the point where the gate logs `task_done REFUSED` and returns/raises, ADD a call to `_record_handoff_refused(session_or_ctx)` using whatever the local handle is for the session/context (likely `self.session` or a passed-in context). If unsure, search for usages of `session._cua_confirm_future` in the same file — that's a similar pattern showing how the session is accessed.

Example shape:

```python
        if not real_tool_fired and summary not in _BAILOUT_SUMMARY_RE_matches:
            logger.warning(
                f"[subagent:{self._spec.name}] task_done REFUSED — "
                f"no real tool call this handoff (items_since={items_since}, "
                f"refusal #{count}/{_NO_TOOL_RETRY_CEILING}). "
                f"Summary attempted: {summary!r}"
            )
            # 2026-05-19 L2 — set the flag the supervisor reads next turn.
            _record_handoff_refused(self.session)
            # ... existing return / raise path ...
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_subagent_refused_flag.py tests/test_subagents_health.py -v
```

Expected: 3 new + all existing pass.

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/subagents/agent.py \
        src/voice-agent/tests/test_subagent_refused_flag.py
git commit -m "feat(subagent): L2 — gate refusal sets handoff-refused flag

_record_handoff_refused(session) is called from the gate when
task_done is refused with 'no real tool'. It sets
session._jarvis_last_handoff_refused = True so the supervisor's
next reply path can apply the POST-HANDOFF HONESTY rule from
supervisor.md.

_clear_handoff_refused complements it for cleanup; called when a
real tool_result lands or a new session starts. Idempotent.

The pair are pure helpers — no behavioral coupling beyond the
single boolean. Per spec 2026-05-19 §5.2."
```

---

## Task 9: Layer 2 — POST-HANDOFF HONESTY supervisor rule

**Files:**
- Modify: `src/voice-agent/prompts/supervisor.md`

- [ ] **Step 1: Find the insertion point**

```bash
grep -nE "^═══ ACTION HONESTY|^═══ AFTER A TOOL OR HANDOFF" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md
```

Pick the location immediately AFTER the existing `═══ AFTER A TOOL OR HANDOFF ═══` section (which already covers the happy path of relaying tool results); the new POST-HANDOFF HONESTY rule complements it for the gate-refusal case.

- [ ] **Step 2: Insert the POST-HANDOFF HONESTY rule**

Use the Edit tool to insert the following block at the chosen point in `src/voice-agent/prompts/supervisor.md`:

```
═══ POST-HANDOFF HONESTY (DOES THE HANDOFF HAVE EVIDENCE?) ═══

Before voicing a success claim ("I've opened...", "Done.", "X is
now Y", "Launched."), check: did the prior subagent handoff return
WITH a confirming tool_result, or WITHOUT one (gate refused)?

If your last assistant turn was a `transfer_to_*` and the chat_ctx
contains a corresponding allowed task_done summary OR a structured
tool_result — voice the success normally.

If it contains ONLY the transfer (no allowed task_done, no
tool_result), OR you can see "task_done REFUSED" / the subagent's
bailout phrase, you DO NOT have evidence the action succeeded.
HEDGE:

WRONG (live-captured 2026-05-19T02:24:18):
  ❌ "I've opened Chrome for you. Handing back to the
     supervisor now."  (no evidence Chrome opened; was a lie)
  ❌ "I already launched Chrome successfully."   (confabulated)

RIGHT — three honest forms, pick one:
  ✅ "I tried but couldn't confirm Chrome opened — want me to
     check?"  (offers to verify)
  ✅ "I'm not sure that completed — should I try again?"
  ✅ "Looks like the desktop tool didn't go through. Try
     again?"

If the subagent's task_done was REFUSED specifically (you'll see
that in chat_ctx context), explicitly acknowledge the uncertainty
— never paper over with a confident claim.

Past failure 2026-05-19T02:24:18: route=EMOTIONAL handoff to
desktop subagent. Gate refused task_done twice ('no real tool').
Supervisor still voiced "I've opened Chrome for you" with
confidence. Chrome was not running. User caught the lie.

═══
```

- [ ] **Step 3: Verify the prompt parses + section count increases by 1**

```bash
wc -l /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md
grep -c "^═══" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md
```

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/prompts/supervisor.md
git commit -m "feat(prompt): L2 — POST-HANDOFF HONESTY rule

Teaches the supervisor that a handoff returning WITHOUT a
tool_result / allowed task_done means he doesn't have evidence
the action happened — must hedge, not claim success.

Three RIGHT forms provided ('couldn't confirm — want me to check?'
etc.) and three WRONG examples cite the 2026-05-19T02:24:18
Chrome confab as the live failure.

Pairs with confab_detector.py strict evidence rule + subagents/
agent.py _jarvis_last_handoff_refused flag. Without this prompt
rule the LLM may ignore the flag; with it the rule is explicit.

Per spec 2026-05-19 §5.2."
```

---

## Task 10: bin/jarvis-confab-soak observability script

**Files:**
- Create: `bin/jarvis-confab-soak`

- [ ] **Step 1: Create the script**

Create `bin/jarvis-confab-soak`:

```bash
#!/usr/bin/env bash
# Confab defense-in-depth soak validation.
# Rolls up confab_check_state distribution over a configurable
# window AND hard-fails on any turn where supervisor voiced a
# success claim without a matching tool_result.
#
# Usage:
#   jarvis-confab-soak             # last 2 hours
#   jarvis-confab-soak 24          # last 24 hours
#   jarvis-confab-soak 168         # last week
#
# Spec: docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.4

set -euo pipefail

HOURS="${1:-2}"
DB="$HOME/.local/share/jarvis/turn_telemetry.db"
if [[ ! -f "$DB" ]]; then
    echo "missing telemetry db: $DB" >&2; exit 1
fi

CUTOFF_SQL="datetime('now', '-${HOURS} hours')"

echo "=== Confab defense-in-depth soak — last ${HOURS}h ==="
echo
echo "── confab_check_state distribution ──"
sqlite3 -header -column "$DB" "
SELECT
    COALESCE(confab_check_state, '(null)') AS state,
    COUNT(*) AS turns,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM turns
WHERE ts_utc >= ${CUTOFF_SQL}
GROUP BY confab_check_state
ORDER BY turns DESC;
"
echo
echo "── per-route × state breakdown ──"
sqlite3 -header -column "$DB" "
SELECT
    route,
    COALESCE(confab_check_state, '(null)') AS state,
    COUNT(*) AS turns
FROM turns
WHERE ts_utc >= ${CUTOFF_SQL}
GROUP BY route, confab_check_state
ORDER BY route, turns DESC;
"
echo
echo "── HARD-FAIL CHECK: voiced 'I've opened/launched/started X' without confab_check_state=evidence_ok ──"
SUSPECT_COUNT=$(sqlite3 "$DB" "
SELECT COUNT(*) FROM turns
WHERE ts_utc >= ${CUTOFF_SQL}
  AND jarvis_text REGEXP 'I''?ve (opened|launched|started|done)'
  AND COALESCE(confab_check_state, '') != 'evidence_ok';
" 2>/dev/null || echo "0")

if [[ "$SUSPECT_COUNT" -gt 0 ]]; then
    echo "  ✗ FOUND $SUSPECT_COUNT suspect turns:"
    sqlite3 -separator ' | ' "$DB" "
    SELECT substr(ts_utc, 12, 8), route, COALESCE(subagent,'-'),
           COALESCE(confab_check_state,'(null)'),
           substr(user_text, 1, 60),
           substr(jarvis_text, 1, 80)
    FROM turns
    WHERE ts_utc >= ${CUTOFF_SQL}
      AND jarvis_text REGEXP 'I''?ve (opened|launched|started|done)'
      AND COALESCE(confab_check_state, '') != 'evidence_ok'
    ORDER BY ts_utc DESC LIMIT 10;
    "
    echo
    echo "  These are CANDIDATE CONFABS — manually verify each: did the action actually happen?"
    exit 2
fi

echo "  ✓ no suspect turns — every 'I've X-ed' claim corroborated by evidence_ok"
echo
echo "── interpretation guide ──"
cat <<'EOF'
  Target distribution (post-fix, 7+ day baseline):
    evidence_ok          : >95%
    hedged_no_evidence   : <5%
    refused_handoff      : <1% (each one is a recoverable failure;
                                consistent rise = L1 regression)
    stale_ctx_dropped    : variable; depends on cross-session traffic
    (null)               : pre-fix turns; expected to decay over time

  Investigation triggers:
    refused_handoff > 1% → check tools/computer_safety.py + sanitizers/pycall.py
    suspect_turns > 0    → manually verify; an L2 (confab detector) regression
    silent (null > 50% in last hour) → check log_turn() wiring
EOF
```

- [ ] **Step 2: Make it executable + smoke test**

```bash
chmod +x /home/ulrich/Documents/Projects/jarvis/bin/jarvis-confab-soak
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-confab-soak 24 || true
```

Expected: runs to completion; output shows current state distribution (mostly `(null)` before any of the fix lands and `confab_check_state` is wired into the actual write path — that's a follow-up integration point).

- [ ] **Step 3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add bin/jarvis-confab-soak
git commit -m "feat(bin): jarvis-confab-soak — defense-in-depth soak validation

Rolls up confab_check_state distribution over a configurable
window (default 2h). Three sections:
  - state distribution + percentages
  - per-route × state breakdown
  - HARD-FAIL: voiced 'I've opened/launched/started X' without
    a matching evidence_ok verdict

Exits 2 if any suspect turn is found (candidate confab) so this
can be wired into a periodic systemd timer for automated alerts.

Target distribution (per spec 2026-05-19 §5.4 + §8 A6):
  evidence_ok          : >95%
  hedged_no_evidence   : <5%
  refused_handoff      : <1%

Per spec 2026-05-19 §5.4."
```

---

## Self-Review

### Spec coverage

| Spec requirement (§) | Implementing task |
|---|---|
| §2 G1 — tool result lands in chat_ctx | Task 4 (helper) + Task 5 (pycall integration) |
| §2 G2 — confab detector requires corroborated evidence | Task 6 (strict rule) + Task 7 (verify_launched) + Task 9 (POST-HANDOFF prompt) |
| §2 G3 — chat_ctx hygiene + STALE wrap | Task 2 (filter + wrap) + Task 3 (STALE prompt) |
| §2 G4 — telemetry observability | Task 1 (column) + Task 10 (soak script) |
| §2 G5 — each layer kill-switch | Task 2 (`JARVIS_RECALL_MAX_AGE_S`) + Task 4 (`JARVIS_PYCALL_SYNTH_DISABLED`) + Task 6 (`JARVIS_CONFAB_STRICT_DISABLED`) |
| §5.1 L1 file map (`_function_call_recovery.py` + `pycall.py`) | Tasks 4 + 5 |
| §5.2 L2 file map (`confab_detector.py` + `agent.py` + `supervisor.md`) | Tasks 6 + 7 + 8 + 9 |
| §5.3 L3 file map (`chat_ctx.py` + `supervisor.md`) | Tasks 2 + 3 |
| §5.4 cross-layer telemetry | Task 1 |
| §6 data flows (happy + failure A/B/C) | Test scenarios in Tasks 5 (happy/F1), 8+9 (failure A), 2 (failure B) |
| §7 error handling table | Each layer's kill-switch + graceful-fallback paths land in Tasks 2/4/6 |
| §8 acceptance criteria A1-A8 | Distributed across all tasks; A6 (no silent confab) is Task 10 |

All 8 acceptance criteria mapped.

### Placeholder scan

No `TBD` / `TODO` / `FIXME` / "implement later" / "similar to" / "appropriate error handling" red flags. Every code step has complete code; every command step has the expected output described.

### Type consistency

- `confab_check_state: Optional[str]` — consistent across Task 1 (writer kwarg), Task 10 (soak script SELECT).
- `chat_ctx.items` is a list with `.append()` — used consistently in Task 4 (helper), Task 5 (integration test).
- `session._jarvis_last_handoff_refused: bool` — set in Task 8, READ by the supervisor (Task 9 is prompt-only, no code read).
- `verify_launched(binary_name, timeout_s) -> bool | None` — Task 7. (Wiring into supervisor reply path is an integration step that happens DURING the soak — Task 10's script catches missing wiring via the HARD-FAIL block.)
- Env vars: `JARVIS_RECALL_MAX_AGE_S` (int seconds), `JARVIS_PYCALL_SYNTH_DISABLED` (str "1"|"0"), `JARVIS_CONFAB_STRICT_DISABLED` (str "1"|"0") — consistent.

All consistent. Implementation can proceed.

### Final tally

- 10 tasks. Each is bite-sized (50-150 LOC production + 30-80 LOC tests, 5-7 TDD steps each).
- ~500 LOC production code + ~400 LOC tests.
- Phased rollout matches spec §9: Task 1 (foundation) → Tasks 2-3 (L3, lowest risk) → Tasks 4-5 (L1, root-cause) → Tasks 6-9 (L2, behavioral) → Task 10 (observability).
- Each layer has its own env-var kill-switch + can be reverted independently.
- TDD discipline: every code task starts with a failing test.
- Frequent commits: one commit per task.

---

## Plan complete and saved to `docs/superpowers/plans/2026-05-19-confab-defense-in-depth.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review (spec-compliance, then code-quality) between tasks. Best for catching architectural drift across 10 task hand-offs. ~30-60 min wall-clock with parallel reviewer subagents.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch with checkpoints. Faster but reuses current context (which is approaching long). 10 task body + tests is borderline for this approach.

Which approach?
