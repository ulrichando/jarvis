# Plan 004: Background agent-restart tasks log their failures (no silent swallow)

> **Executor instructions**: Follow step by step, run each verify command, honor
> STOP conditions. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat f6efd301..HEAD -- src/voice-agent/voice_client_http_api.py src/voice-agent/voice_client_watchdog.py`
> If either changed, re-read the cited lines before editing.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (adds a done-callback + logging; does not change restart behavior)
- **Depends on**: none
- **Category**: bug (silent failure)
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

The agent restart is launched fire-and-forget:
`asyncio.create_task(self.restart_agent_unit())` with no done-callback. If the
coroutine raises (systemd unreachable, permission denied, dbus error), Python
discards the exception (at best a "Task exception was never retrieved" warning at
GC time) and the HTTP handler has already returned `{"restarting": true}`. The
user sees the tray pill flip to "JARVIS booting" and hang forever, with nothing
actionable in the logs. This plan attaches a done-callback that logs the failure,
so a failed restart is diagnosable. Restart success path is unchanged.

## Current state

Three fire-and-forget sites, all calling the same coroutine:

- `src/voice-agent/voice_client_http_api.py:766` (after a speech-model switch):
  ```python
              # Fire-and-forget — agent restart takes ~3-5 s; the user
              # sees the pill flip to amber "JARVIS booting" and back to green.
              asyncio.create_task(self.restart_agent_unit())
  ```
- `src/voice-agent/voice_client_http_api.py:824` (after a TTS-provider switch):
  ```python
              asyncio.create_task(self.restart_agent_unit())
  ```
- `src/voice-agent/voice_client_watchdog.py:285` (stale-STT self-heal):
  ```python
              asyncio.create_task(self.restart_agent_unit(), name="stale-stt-restart")
  ```

**Conventions**: the voice-agent runs with `src/voice-agent/` as the working
directory, so top-level modules import by bare name (e.g. `from confab_detector
import ...`). Logging is via the stdlib `logging` module. Tests live in
`src/voice-agent/tests/` and run with `.venv/bin/python -m pytest tests/`.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Find each module's logger | `grep -n 'getLogger\|^log =\|^logger =\|self\._log\|self\.log' src/voice-agent/voice_client_http_api.py src/voice-agent/voice_client_watchdog.py` | the logger handle(s) |
| Run the new unit test | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_task_utils.py -q` | passes |
| Run the affected suites | `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q -k 'task_utils or http_api or watchdog'` | passes |

## Scope

**In scope**:
- `src/voice-agent/_task_utils.py` (create — a tiny shared helper)
- `src/voice-agent/voice_client_http_api.py` (wrap the two `create_task` calls)
- `src/voice-agent/voice_client_watchdog.py` (wrap the one `create_task` call)
- `src/voice-agent/tests/test_task_utils.py` (create)

**Out of scope** (do NOT touch):
- `restart_agent_unit` itself — its logic is fine; we only observe its failures.
- Any other `asyncio.create_task` call elsewhere in the repo — only the three
  restart sites above. (Adding the helper broadly is a separate cleanup.)

## Git workflow

- Branch: `advisor/004-restart-task-logging`
- One commit, e.g. `fix(voice): log background restart-task failures`.
- Do NOT push / open a PR unless instructed.

## Steps

### Step 1: Add the shared helper `_task_utils.py`

Create `src/voice-agent/_task_utils.py`:

```python
"""Small asyncio helpers. Leaf module — no project imports (avoids cycles)."""
from __future__ import annotations

import asyncio
import logging

_log = logging.getLogger(__name__)


def log_task_exception(task: "asyncio.Task") -> None:
    """done_callback that surfaces a fire-and-forget task's exception.

    Without this, an exception raised inside a bare ``create_task`` coroutine is
    swallowed (only a GC-time 'never retrieved' warning). Attach via
    ``task.add_done_callback(log_task_exception)``.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _log.error("background task %r failed: %s", task.get_name(), exc, exc_info=exc)
```

**Verify**: `cd src/voice-agent && .venv/bin/python -c "import _task_utils; print('ok')"` → `ok`.

### Step 2: Wrap the two http_api restart calls

In `src/voice-agent/voice_client_http_api.py`, add `from _task_utils import
log_task_exception` near the other imports. Replace each of the two
`asyncio.create_task(self.restart_agent_unit())` calls (lines ~766, ~824) with:

```python
            _t = asyncio.create_task(self.restart_agent_unit(), name="agent-restart")
            _t.add_done_callback(log_task_exception)
```

(Keep the surrounding comment/response code unchanged.)

**Verify**: `grep -n 'add_done_callback(log_task_exception)' src/voice-agent/voice_client_http_api.py` → 2 matches; and `cd src/voice-agent && .venv/bin/python -c "import ast,sys; ast.parse(open('voice_client_http_api.py').read()); print('parse-ok')"` → `parse-ok`.

### Step 3: Wrap the watchdog restart call

In `src/voice-agent/voice_client_watchdog.py`, add the same import and change the
line-285 call to attach the callback (it already names the task):

```python
            _t = asyncio.create_task(self.restart_agent_unit(), name="stale-stt-restart")
            _t.add_done_callback(log_task_exception)
```

**Verify**: `grep -n 'add_done_callback(log_task_exception)' src/voice-agent/voice_client_watchdog.py` → 1 match; parse-check the file as in Step 2.

### Step 4: Unit-test the helper

Create `src/voice-agent/tests/test_task_utils.py` (model imports/style after an
existing test in `src/voice-agent/tests/`):

```python
import asyncio
import logging

import pytest

from _task_utils import log_task_exception


@pytest.mark.asyncio
async def test_logs_on_failure(caplog):
    async def boom():
        raise RuntimeError("nope")
    t = asyncio.create_task(boom(), name="t-fail")
    with pytest.raises(RuntimeError):
        await t
    with caplog.at_level(logging.ERROR):
        log_task_exception(t)
    assert any("failed" in r.message or "nope" in str(r.exc_info) for r in caplog.records)


@pytest.mark.asyncio
async def test_silent_on_success():
    async def ok():
        return 1
    t = asyncio.create_task(ok(), name="t-ok")
    await t
    log_task_exception(t)  # must not raise
```

If the suite doesn't have `pytest-asyncio` configured, check
`src/voice-agent/pytest.ini` for an `asyncio_mode` setting and follow the pattern
an existing async test uses (don't add new plugins).

**Verify**: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_task_utils.py -q` → all pass.

## Test plan

- New `tests/test_task_utils.py`: (1) failed task → an ERROR log is emitted;
  (2) successful task → no error, no raise. Pattern: an existing async test in
  `src/voice-agent/tests/`.
- Verification: the targeted pytest above, then
  `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` shows no new failures.

## Done criteria

- [ ] `_task_utils.py` imports cleanly.
- [ ] Both http_api calls and the watchdog call attach `log_task_exception`
      (`grep -rn 'add_done_callback(log_task_exception)' src/voice-agent` → 3 matches).
- [ ] `tests/test_task_utils.py` passes.
- [ ] `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → no new failures.
- [ ] `git status` shows only the 4 in-scope files.
- [ ] `plans/README.md` row for 004 updated.

## STOP conditions

- Importing `_task_utils` from the two modules creates a circular import → STOP
  (shouldn't happen — it's a leaf module — report if it does).
- `restart_agent_unit` is not a method on `self` in one of the files (signature
  drift) → STOP and report.
- The pytest suite has no async support and no existing async test to copy →
  STOP; report so a maintainer can advise on the async test setup.

## Maintenance notes

- This helper is reusable: other fire-and-forget `create_task` sites in the
  voice-agent could adopt it in a later sweep (out of scope here).
- Reviewer: confirm the callback is attached to the SAME task object returned by
  `create_task` (not a re-created one), and that the response JSON
  (`{"restarting": true}`) is unchanged — the UX contract depends on it.
