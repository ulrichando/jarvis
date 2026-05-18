# Computer-Use Password-Check Fail-Open Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound the per-iteration password-check at ≤1.5 s wall-clock with fail-open fallback, eliminating the ~10 s/step bottleneck in the computer-use loop.

**Architecture:** Wrap the existing `_gemini_password_check` Gemini Flash Lite call in `asyncio.wait_for(..., timeout=1.5)`. New `check_password_visible(png, widgets) -> tuple[bool, str]` returns the visibility decision AND a state tag (`fastpath_hit | fastpath_miss | slowpath | failopen`) so per-action audit rows record which path fired. Legacy `is_password_field_visible -> bool` stays as a back-compat wrapper. New SQLite column `pwd_check_state` on `computer_use_actions` makes fail-open ratio queryable.

**Tech Stack:** Python 3.13 (voice-agent venv at `src/voice-agent/.venv/`), `asyncio.wait_for` for timeout, pytest with `@pytest.mark.asyncio`, existing `tools._vision_backend.describe_image` for Gemini Flash Lite (unchanged).

**Spec:** [`docs/superpowers/specs/2026-05-18-cua-password-check-failopen-design.md`](../specs/2026-05-18-cua-password-check-failopen-design.md) — read §4 for component interfaces and §5 for error handling semantics.

**Environment flag:** `JARVIS_PASSWORD_CHECK_STRICT=1` flips to fail-closed (opt-in, default OFF). `JARVIS_PASSWORD_CHECK_TIMEOUT_S=<float>` overrides the 1.5 s ceiling (default OFF, soak-tested at 1.5 s).

---

## File Structure

### Files modified

| Path | Change |
|---|---|
| `src/voice-agent/tools/computer_safety.py` | Add module-level `_GEMINI_TIMEOUT_S` constant + new `check_password_visible(png, widgets) -> tuple[bool, str]` with `asyncio.wait_for` wrapping. Existing `is_password_field_visible(png, widgets) -> bool` becomes a thin back-compat wrapper. |
| `src/voice-agent/pipeline/turn_telemetry.py` | Online migration: add `pwd_check_state TEXT` column to `computer_use_actions`. Extend `log_computer_use_action(..., pwd_check_state=None)` kwarg + INSERT. |
| `src/voice-agent/tools/computer_loop.py` | New seam `_check_password_visible` (parallel to the legacy `_is_password_visible`). Replace call site to use the new seam, thread `pw_state` into the `_log_action(...)` call on the blocked-bail path AND on the action-success path. |
| `src/voice-agent/tests/test_computer_safety.py` | 5 new tests covering each state-tree branch + strict mode. |
| `src/voice-agent/tests/test_computer_use_telemetry.py` | 1 new test verifying the migration lands. |
| `src/voice-agent/tests/test_computer_loop.py` | Update `test_loop_blocks_on_password_field` mock to return the tuple shape. |

### No new files

All changes land in existing files. ~50 LOC production code + ~120 LOC tests.

---

## Task 1: Telemetry migration + writer kwarg

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (add column + extend writer)
- Test: `src/voice-agent/tests/test_computer_use_telemetry.py` (1 new test)

- [ ] **Step 1: Write the failing test**

Append to `src/voice-agent/tests/test_computer_use_telemetry.py`:

```python
def test_migration_adds_pwd_check_state_column(tmp_path):
    """2026-05-18 — pwd_check_state on computer_use_actions lets the
    operator query fast-path vs slow-path vs fail-open ratios."""
    db = tmp_path / "tele.db"
    init_db(db)
    cols = {
        r[1]
        for r in sqlite3.connect(db).execute(
            "PRAGMA table_info(computer_use_actions)"
        )
    }
    assert "pwd_check_state" in cols


def test_log_computer_use_action_persists_pwd_check_state(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    from pipeline.turn_telemetry import log_computer_use_action
    log_computer_use_action(
        db_path=db,
        handoff_id="abc123",
        step=1,
        model_used="claude-sonnet-4-6",
        action="screenshot",
        success=True,
        pwd_check_state="fastpath_hit",
    )
    row = sqlite3.connect(db).execute(
        "SELECT pwd_check_state FROM computer_use_actions WHERE handoff_id='abc123'"
    ).fetchone()
    assert row == ("fastpath_hit",)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_computer_use_telemetry.py::test_migration_adds_pwd_check_state_column tests/test_computer_use_telemetry.py::test_log_computer_use_action_persists_pwd_check_state -v
```

Expected: both FAIL — first with `assert "pwd_check_state" in cols` (column not present), second with `TypeError` on the unknown kwarg.

- [ ] **Step 3: Add the column to `init_db`**

In `src/voice-agent/pipeline/turn_telemetry.py`, find the `init_db()` function. After the existing `computer_use_actions` table creation (the `conn.executescript("""CREATE TABLE IF NOT EXISTS computer_use_actions...""")` block), ADD a new migration block:

```python
        # 2026-05-18 — pwd_check_state per-action audit. Lets the operator
        # query fast-path/slow-path/fail-open ratios over time and alert
        # when fail-open exceeds the soak-acceptable threshold (~5%).
        # Spec: docs/superpowers/specs/2026-05-18-cua-password-check-failopen-design.md
        cua_cols = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(computer_use_actions)"
            )
        }
        if "pwd_check_state" not in cua_cols:
            try:
                conn.execute(
                    "ALTER TABLE computer_use_actions ADD COLUMN pwd_check_state TEXT"
                )
            except sqlite3.OperationalError:
                pass
```

Place this AFTER the existing `computer_use_actions` CREATE block but BEFORE any subsequent `CREATE INDEX` lines so the column is present when later code queries it.

- [ ] **Step 4: Extend `log_computer_use_action` signature + INSERT**

In the same file, find `def log_computer_use_action(`. Add a new kwarg at the END of the parameter list (after `notes`):

```python
def log_computer_use_action(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    handoff_id: str,
    step: int,
    model_used: Optional[str],
    action: str,
    params_json: Optional[str] = None,
    success: bool = True,
    screenshot_path: Optional[str] = None,
    notes: Optional[str] = None,
    pwd_check_state: Optional[str] = None,
) -> None:
```

Update the INSERT statement to include the new column. Find the existing INSERT — it's a multi-line string that lists columns then VALUES. Update it to:

```python
            conn.execute(
                """INSERT INTO computer_use_actions
                   (ts_utc, handoff_id, step, model_used, action,
                    params_json, success, screenshot_path, notes,
                    pwd_check_state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    handoff_id, step, model_used, action,
                    params_json, int(success), screenshot_path, notes,
                    pwd_check_state,
                ),
            )
```

(Note the addition of one new column name in the column-list, one new `?` placeholder in VALUES, and one new value in the tuple.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_computer_use_telemetry.py -v
```

Expected: All telemetry tests pass (existing + 2 new).

- [ ] **Step 6: Run existing telemetry suite to confirm no regression**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_turn_telemetry.py -v
```

Expected: all existing turn_telemetry tests still pass.

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/turn_telemetry.py \
        src/voice-agent/tests/test_computer_use_telemetry.py
git commit -m "feat(telemetry): pwd_check_state column on computer_use_actions

Online migration adds a TEXT column to computer_use_actions for
per-action password-check audit state ('fastpath_hit' |
'fastpath_miss' | 'slowpath' | 'failopen'). log_computer_use_action()
accepts a new optional pwd_check_state kwarg. Lets the operator
query fail-open ratios over time:

  SELECT pwd_check_state, COUNT(*) FROM computer_use_actions
  GROUP BY pwd_check_state;

Per spec 2026-05-18 §4 schema migrations."
```

---

## Task 2: `check_password_visible` with bounded timeout

**Files:**
- Modify: `src/voice-agent/tools/computer_safety.py` (add new function + back-compat wrapper)
- Test: `src/voice-agent/tests/test_computer_safety.py` (5 new tests)

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_computer_safety.py`:

```python
# ── check_password_visible (2026-05-18 fail-open hardening) ──

@pytest.mark.asyncio
async def test_check_password_visible_fastpath_hit():
    """AT-SPI password_text widget → instant return (no Gemini call)."""
    from tools.computer_safety import check_password_visible
    widgets = [_widget("password_text", "")]
    visible, state = await check_password_visible(png=b"", widgets=widgets)
    assert visible is True
    assert state == "fastpath_hit"


@pytest.mark.asyncio
async def test_check_password_visible_fastpath_miss():
    """AT-SPI returned other widgets but no password_text → instant False."""
    from tools.computer_safety import check_password_visible
    widgets = [_widget("text", "user@example.com")]
    visible, state = await check_password_visible(png=b"", widgets=widgets)
    assert visible is False
    assert state == "fastpath_miss"


@pytest.mark.asyncio
async def test_check_password_visible_slowpath_success(monkeypatch):
    """AT-SPI empty + Gemini returns True quickly → state='slowpath'."""
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def fast_gemini(png):
        return True
    monkeypatch.setattr(computer_safety, "_gemini_password_check", fast_gemini)
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is True
    assert state == "slowpath"


@pytest.mark.asyncio
async def test_check_password_visible_failopen_on_timeout(monkeypatch):
    """AT-SPI empty + Gemini hangs past timeout → fail OPEN (False, 'failopen')."""
    import asyncio
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def slow_gemini(png):
        await asyncio.sleep(10.0)
        return True
    monkeypatch.setattr(computer_safety, "_gemini_password_check", slow_gemini)
    monkeypatch.setattr(computer_safety, "_GEMINI_TIMEOUT_S", 0.05)
    monkeypatch.delenv("JARVIS_PASSWORD_CHECK_STRICT", raising=False)
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is False  # default: fail-open
    assert state == "failopen"


@pytest.mark.asyncio
async def test_check_password_visible_failopen_strict_mode(monkeypatch):
    """STRICT=1 + timeout → fail CLOSED (returns True so loop bails)."""
    import asyncio
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def slow_gemini(png):
        await asyncio.sleep(10.0)
        return False
    monkeypatch.setattr(computer_safety, "_gemini_password_check", slow_gemini)
    monkeypatch.setattr(computer_safety, "_GEMINI_TIMEOUT_S", 0.05)
    monkeypatch.setenv("JARVIS_PASSWORD_CHECK_STRICT", "1")
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is True  # strict: fail-closed
    assert state == "failopen"


@pytest.mark.asyncio
async def test_check_password_visible_failopen_on_exception(monkeypatch):
    """AT-SPI empty + Gemini raises → fail-open (default mode)."""
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def broken_gemini(png):
        raise RuntimeError("provider unreachable")
    monkeypatch.setattr(computer_safety, "_gemini_password_check", broken_gemini)
    monkeypatch.delenv("JARVIS_PASSWORD_CHECK_STRICT", raising=False)
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is False
    assert state == "failopen"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_computer_safety.py -v -k "check_password_visible"
```

Expected: 6 FAILs — `check_password_visible` doesn't exist yet (`ImportError` on each test's `from tools.computer_safety import check_password_visible`).

- [ ] **Step 3: Add `_GEMINI_TIMEOUT_S` constant + `check_password_visible` function**

In `src/voice-agent/tools/computer_safety.py`, just below the existing constants (after `_DESTRUCTIVE_SHELL_RE`), ADD:

```python
# Hard timeout for the Gemini fallback in check_password_visible.
# Research-validated 2026-05-18: 1.5s preserves the 30-iter loop's
# wall-clock budget (30 × 1.5 = 45s worst case vs 30 × 10 = 300s
# without the cap). Tighter values (e.g. 0.8s) save more wall-clock
# but increase fail-open ratio on slow Gemini days. Overridable via
# env: JARVIS_PASSWORD_CHECK_TIMEOUT_S. Spec:
# docs/superpowers/specs/2026-05-18-cua-password-check-failopen-design.md
import os as _os
_GEMINI_TIMEOUT_S: float = float(
    _os.environ.get("JARVIS_PASSWORD_CHECK_TIMEOUT_S", "1.5")
)
```

After the existing `_gemini_password_check` function definition, ADD the new function:

```python
async def check_password_visible(
    png: bytes, widgets: list[Widget]
) -> tuple[bool, str]:
    """Two-layer password-field detection with bounded latency.

    Returns (visible, state) where state is one of:
      - "fastpath_hit":   AT-SPI saw a password_text widget. Microseconds.
      - "fastpath_miss":  AT-SPI returned widgets but none were password
                          fields. Microseconds.
      - "slowpath":       AT-SPI empty; Gemini fallback ran and answered
                          within _GEMINI_TIMEOUT_S. ~hundreds of ms in
                          the happy case.
      - "failopen":       Gemini timed out or raised. Returns False
                          (fail-open) by default, True (fail-closed)
                          when JARVIS_PASSWORD_CHECK_STRICT=1.

    Rationale: Anthropic's reference computer_use_demo/loop.py ships
    NO client-side password check — they trust model training plus a
    server-side prompt-injection classifier. JARVIS's check is
    defense-in-depth that MUST NOT dominate latency. Per the
    2026-05-18 industry-validation research and OS-Harm benchmark
    (arxiv 2506.14866), fail-open on this layer is correct because
    Sonnet 4.6's own training is the primary defense.
    """
    # Layer 1 — AT-SPI fast path (microseconds)
    for w in widgets:
        if w.role == "password_text":
            return True, "fastpath_hit"
    if widgets:
        return False, "fastpath_miss"

    # Layer 2 — Gemini fallback (bounded by _GEMINI_TIMEOUT_S)
    import asyncio as _asyncio
    import hashlib as _hashlib
    import time as _time
    started = _time.monotonic()
    try:
        result = await _asyncio.wait_for(
            _gemini_password_check(png),
            timeout=_GEMINI_TIMEOUT_S,
        )
        return bool(result), "slowpath"
    except (_asyncio.TimeoutError, Exception) as e:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        strict = _os.environ.get("JARVIS_PASSWORD_CHECK_STRICT") == "1"
        shot_hash = _hashlib.md5(png).hexdigest()[:12] if png else "empty"
        logger.warning(
            f"[computer_safety] password check failed open "
            f"(cause={type(e).__name__}, elapsed_ms={elapsed_ms}, "
            f"shot_hash={shot_hash}, widgets_count={len(widgets)}, "
            f"strict_mode={strict})"
        )
        return strict, "failopen"
```

Then convert the existing `is_password_field_visible` into a back-compat wrapper. Find:

```python
async def is_password_field_visible(
    png: bytes, widgets: list[Widget]
) -> bool:
    """True if the screen appears to have a focused password input.

    Two-layer check:
      Layer 1: any widget with role == "password_text" (AT-SPI).
      Layer 2: Gemini Flash Lite on the screenshot — only consulted
               when AT-SPI returned no widgets at all (sparse
               accessibility tree).
    """
    for w in widgets:
        if w.role == "password_text":
            return True
    if not widgets:
        # AT-SPI is sparse — fall back to vision.
        return await _gemini_password_check(png)
    # AT-SPI returned widgets but no password_text → trust it.
    return False
```

Replace with:

```python
async def is_password_field_visible(
    png: bytes, widgets: list[Widget]
) -> bool:
    """Back-compat wrapper around check_password_visible.

    Existing callers that only need the bool (no state tag) get the
    same semantics. New callers should use check_password_visible
    directly to capture the state for audit logging.

    Note: this wrapper inherits the bounded-latency behaviour of
    check_password_visible — the unbounded Gemini call that existed
    pre-2026-05-18 is gone. Callers that relied on infinite waits will
    now fail-open on Gemini timeout (or fail-closed if
    JARVIS_PASSWORD_CHECK_STRICT=1).
    """
    visible, _state = await check_password_visible(png, widgets)
    return visible
```

Update `__all__` to include the new function:

```python
__all__ = [
    "parse_destructive_intent",
    "is_password_field_visible",
    "check_password_visible",
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_computer_safety.py -v
```

Expected: all tests pass — existing (`is_password_field_visible` tests still green via the back-compat wrapper) + 6 new (`check_password_visible` tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tools/computer_safety.py \
        src/voice-agent/tests/test_computer_safety.py
git commit -m "feat(computer_use): bounded-latency check_password_visible

New function check_password_visible(png, widgets) returns
(visible, state) with state ∈ {fastpath_hit, fastpath_miss,
slowpath, failopen}. Gemini fallback wrapped in
asyncio.wait_for(timeout=1.5s) — fails OPEN on timeout/error by
default, fails CLOSED when JARVIS_PASSWORD_CHECK_STRICT=1.
Structured WARN log on every fail-open.

Existing is_password_field_visible(png, widgets) -> bool retained
as a back-compat wrapper around check_password_visible — all current
callers (and tests) continue to work with the same public API,
inheriting the new bounded-latency behaviour.

Per spec 2026-05-18 §4 tools/computer_safety.py. Research-validated
against Anthropic's reference loop.py (which ships no client-side
check at all) and OS-Harm benchmark (model training catches 100% of
'send password by email' but only 60% of 'leak via URL' — so the
defense-in-depth value is real, it just must not block the loop)."
```

---

## Task 3: Loop call-site update + audit-row threading

**Files:**
- Modify: `src/voice-agent/tools/computer_loop.py` (new seam + thread state through `_log_action`)
- Test: `src/voice-agent/tests/test_computer_loop.py` (update existing test)

- [ ] **Step 1: Write the failing test update**

In `src/voice-agent/tests/test_computer_loop.py`, find the existing `test_loop_blocks_on_password_field` test. Replace its body with:

```python
@pytest.mark.asyncio
async def test_loop_blocks_on_password_field(loop_env, monkeypatch):
    """If password is visible at the start of an iteration, hard-stop
    with reason='blocked' before calling Anthropic. With the
    2026-05-18 fail-open hardening, the seam now returns a tuple
    (visible, state); the loop threads `state` into the audit row."""
    from tools.computer_loop import run
    from tools import computer_loop

    script, calls, audit = loop_env

    # Make check_password_visible return (True, "slowpath")
    async def fake_check(png, widgets):
        return True, "slowpath"
    monkeypatch.setattr(
        computer_loop, "_check_password_visible", fake_check
    )

    cancel = asyncio.Event()
    result = await run(
        task="login", anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
    )

    assert result.reason == "blocked"
    assert "password" in result.summary.lower()
    assert len(calls) == 0   # never called Anthropic

    # The bail audit row should carry the password-check state
    bail_rows = [a for a in audit if a.get("action") == "bail"]
    assert bail_rows, "expected a bail audit row"
    assert bail_rows[0].get("pwd_check_state") == "slowpath"
```

Also append a new test that exercises the fast-path threading on a non-blocked iteration:

```python
@pytest.mark.asyncio
async def test_loop_records_pwd_check_state_on_success(loop_env, monkeypatch):
    """Even when the password check is NEGATIVE (allow the action),
    the state ('fastpath_miss' or 'slowpath') must be threaded into
    the per-action audit row."""
    from tools.computer_loop import run
    from tools import computer_loop

    script, calls, audit = loop_env

    async def fake_check(png, widgets):
        return False, "fastpath_miss"
    monkeypatch.setattr(
        computer_loop, "_check_password_visible", fake_check
    )

    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [50, 50]})],
        usage=FakeUsage(),
    ))
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "task_done", "summary": "Done."})],
        usage=FakeUsage(),
    ))

    cancel = asyncio.Event()
    result = await run(
        task="click something",
        anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
    )

    assert result.ok is True
    assert result.reason == "completed"
    # Each non-bail audit row should have the password-check state.
    action_rows = [
        a for a in audit
        if a.get("action") in {"left_click", "task_done"}
    ]
    assert action_rows
    for row in action_rows:
        assert row.get("pwd_check_state") == "fastpath_miss"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_computer_loop.py -v -k "password or pwd_check"
```

Expected:
- `test_loop_blocks_on_password_field` FAIL — `_check_password_visible` seam doesn't exist yet.
- `test_loop_records_pwd_check_state_on_success` FAIL — same.

- [ ] **Step 3: Add the `_check_password_visible` seam**

In `src/voice-agent/tools/computer_loop.py`, find the existing seam declarations near the top of the module (next to `_is_password_visible`). ADD a new seam:

```python
_check_password_visible: Optional[Callable[..., Awaitable[tuple[bool, str]]]] = None
```

In `_bind_production_seams()`, find the existing line that imports `is_password_field_visible` and binds `_is_password_visible`. ADD parallel binding for the new function:

```python
    from tools.computer_safety import (
        is_password_field_visible,
        parse_destructive_intent,
        check_password_visible,
    )

    global _is_password_visible, _parse_destructive, _check_password_visible
    _is_password_visible = is_password_field_visible
    _parse_destructive = parse_destructive_intent
    _check_password_visible = check_password_visible
```

- [ ] **Step 4: Replace the call site to use the new seam + thread state**

In the `run()` function body, find the existing password pre-check (at the top of the iteration body, after the wall-clock + cancel checks). It currently looks like:

```python
        # Safety pre-check: password field visible → hard-stop
        pw_visible = await _is_password_visible(scaled, widgets)
        if pw_visible:
            logger.warning(
                f"[cua:{handoff_id}] password field visible — hard-stop"
            )
            _log_action(
                handoff_id=handoff_id, step=iteration,
                model_used=active_model, action="bail",
                params_json=json.dumps({"reason": "password_visible"}),
                success=False, notes="password field detected; aborting",
            )
            return LoopResult(
                ok=False,
                summary="password / sensitive screen detected — handing back to supervisor",
                steps=steps, cost_usd=cost_usd,
                reason="blocked", handoff_id=handoff_id,
            )
```

Replace with:

```python
        # Safety pre-check: password field visible → hard-stop.
        # check_password_visible has bounded latency (≤1.5s) and
        # returns a state tag for audit. See tools/computer_safety.py.
        pw_visible, pw_state = await _check_password_visible(scaled, widgets)
        if pw_visible:
            logger.warning(
                f"[cua:{handoff_id}] password field visible — hard-stop "
                f"(state={pw_state})"
            )
            _log_action(
                handoff_id=handoff_id, step=iteration,
                model_used=active_model, action="bail",
                params_json=json.dumps({"reason": "password_visible"}),
                success=False, notes="password field detected; aborting",
                pwd_check_state=pw_state,
            )
            return LoopResult(
                ok=False,
                summary="password / sensitive screen detected — handing back to supervisor",
                steps=steps, cost_usd=cost_usd,
                reason="blocked", handoff_id=handoff_id,
            )
```

- [ ] **Step 5: Thread `pw_state` into the action-execution audit row**

In the same iteration body, find the `_log_action` call that runs after `_execute_action`. It currently looks like:

```python
        _log_action(
            handoff_id=handoff_id, step=iteration,
            model_used=active_model, action=action_name,
            params_json=json.dumps(action_input),
            success=success, notes=notes,
            screenshot_path=screenshot_path,
        )
```

Add `pwd_check_state=pw_state` as a new kwarg:

```python
        _log_action(
            handoff_id=handoff_id, step=iteration,
            model_used=active_model, action=action_name,
            params_json=json.dumps(action_input),
            success=success, notes=notes,
            screenshot_path=screenshot_path,
            pwd_check_state=pw_state,
        )
```

Also find the `task_done` exit path's `_log_action` call (the one that fires when `action_name == "task_done"`). Add the same kwarg:

```python
            _log_action(
                handoff_id=handoff_id, step=iteration,
                model_used=active_model, action="task_done",
                params_json=json.dumps(action_input),
                success=True,
                pwd_check_state=pw_state,
            )
```

And the destructive-intent-declined audit row (in the `if not user_ok:` branch). Add the kwarg:

```python
                _log_action(
                    handoff_id=handoff_id, step=iteration,
                    model_used=active_model, action=action_name,
                    params_json=json.dumps(action_input),
                    success=False, notes="user declined destructive action",
                    screenshot_path=screenshot_path,
                    pwd_check_state=pw_state,
                )
```

(If the destructive-decline path re-screenshots BEFORE writing the audit, the `screenshot_path` is the re-screenshot's path — leave that logic as-is. The new `pwd_check_state` reflects the check that happened at the TOP of THIS iteration, which is correct.)

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_computer_loop.py -v
```

Expected: all 10 loop tests pass (the 2 new/updated password tests + 8 existing).

- [ ] **Step 7: Run the FULL voice-agent suite to confirm no regressions**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ --timeout=60 -q \
  --deselect tests/test_browser_subagent.py::test_browser_spec_loads_all_ext_tools 2>&1 | tail -5
```

Expected: all pass except the pre-existing browser-subagent deselected test.

- [ ] **Step 8: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tools/computer_loop.py \
        src/voice-agent/tests/test_computer_loop.py
git commit -m "feat(computer_use): thread pwd_check_state through audit rows

Loop now uses _check_password_visible (the new bounded-latency
seam) and threads the returned state ('fastpath_hit' |
'fastpath_miss' | 'slowpath' | 'failopen') into every _log_action
call within an iteration: bail row on hard-stop, action-execute
row on the success path, task_done row on completion, and the
user-declined row on destructive-action skips.

Tests:
  - test_loop_blocks_on_password_field updated to mock the new
    seam shape (returns tuple), assert pwd_check_state lands in
    the bail audit row.
  - test_loop_records_pwd_check_state_on_success (new): negative
    check still threads the state through the action-success
    audit rows.

Per spec 2026-05-18 §4 tools/computer_loop.py call site."
```

---

## Self-Review

### Spec coverage scan

| Spec requirement | Implementing task |
|---|---|
| §2.1 `check_password_visible(png, widgets) -> tuple[bool, str]` exists | Task 2 |
| §2.2 Gemini wrapped in `asyncio.wait_for(timeout=_GEMINI_TIMEOUT_S=1.5)` | Task 2 step 3 |
| §2.3 `is_password_field_visible` retained as back-compat wrapper | Task 2 step 3 |
| §2.4 `computer_loop.py` uses `check_password_visible` + threads state to `_log_action` | Task 3 steps 3–5 |
| §2.5 `pwd_check_state TEXT` migration on `computer_use_actions` | Task 1 step 3 |
| §2.6 Structured WARN log on every fail-open | Task 2 step 3 (`logger.warning(...)`) |
| §2.7 5 new tests in `test_computer_safety.py` + 1 in telemetry test file | Task 2 step 1 (5+1) + Task 1 step 1 (2 actually — extra coverage on writer) |
| §2.8 `pwd_check_state` queryable via SQL | Task 1 (column landed) + Task 3 (write path threads it) |

All 8 acceptance criteria mapped. Telemetry has 2 tests in Task 1 (not 1 as spec said) — that's a small over-delivery on writer coverage, not a gap.

### Placeholder scan

Grep terms: `TBD`, `TODO`, `FIXME`, `XXX`, `<placeholder`, "Similar to Task". Manual check: none present. Every code step has complete code. Every command step has the exact command + expected output.

### Type consistency

- `check_password_visible` returns `tuple[bool, str]` everywhere — declared in Task 2 step 1 (tests), Task 2 step 3 (impl), Task 3 step 1 (mocks), Task 3 step 3 (seam type).
- `pwd_check_state` is `Optional[str]` (defaults to `None`) — consistent across Task 1 (writer kwarg), Task 3 (call site).
- `_GEMINI_TIMEOUT_S` is a float; default 1.5; monkey-patched to 0.05 in fail-open tests.
- `_check_password_visible` seam type matches the production function (`Optional[Callable[..., Awaitable[tuple[bool, str]]]]`).

All consistent.

### Final tally

- 3 tasks, ~20 TDD steps, ~3-5 minutes each.
- ~170 lines total: ~50 production + ~120 tests.
- Single commit per task. Frequent commits maintained.
- All implementation tasks follow failing-test-first TDD.
- Back-compat wrapper preserves the existing public API — no other JARVIS code needs updating.

---

## Plan complete and saved to `docs/superpowers/plans/2026-05-18-cua-password-check-failopen.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for catching architectural drift early.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster wall-clock but reuses current context.

Which approach?
