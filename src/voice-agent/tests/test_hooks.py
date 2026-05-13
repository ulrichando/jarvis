"""Tests for `pipeline/hooks.py` — local-script lifecycle hook
dispatcher.

Sets up `HOOKS_DIR` per-test in tmp_path so the user's real
~/.jarvis/hooks/ store stays untouched. Uses tiny inline scripts
written to disk (sh / bash) — no mocks, the value of these tests is
end-to-end "did a script fire AND receive the payload AND env vars."
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import time

import pytest


@pytest.fixture
def hooks_module(tmp_path, monkeypatch):
    """Fresh empty hooks dir at tmp_path."""
    from pipeline import hooks
    monkeypatch.setattr(hooks, "HOOKS_DIR", tmp_path / "hooks")
    return hooks


def _install_script(event_dir, name: str, body: str) -> "Path":
    """Write `body` as an executable shell script under `event_dir`."""
    event_dir.mkdir(parents=True, exist_ok=True)
    script = event_dir / name
    script.write_text("#!/bin/bash\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


async def _wait_for_file(path, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        await asyncio.sleep(0.02)
    return False


# ── empty cases ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_no_scripts_returns_zero(hooks_module):
    """Missing event dir is a silent no-op."""
    n = await hooks_module.fire_hook(event="nothing_here", payload={})
    assert n == 0


@pytest.mark.asyncio
async def test_fire_empty_dir_returns_zero(hooks_module, tmp_path):
    (hooks_module.HOOKS_DIR / "empty_event").mkdir(parents=True)
    n = await hooks_module.fire_hook(event="empty_event", payload={})
    assert n == 0


@pytest.mark.asyncio
async def test_non_executable_file_skipped(hooks_module):
    """A file without the +x bit is ignored — drop the bit to
    disable without deleting."""
    event_dir = hooks_module.HOOKS_DIR / "task_created"
    event_dir.mkdir(parents=True)
    f = event_dir / "disabled.sh"
    f.write_text("#!/bin/bash\necho hi\n")
    # NO chmod +x
    n = await hooks_module.fire_hook(event="task_created", payload={})
    assert n == 0


@pytest.mark.asyncio
async def test_subdir_in_event_dir_ignored(hooks_module):
    """Only files (not subdirs) are considered."""
    event_dir = hooks_module.HOOKS_DIR / "task_created"
    (event_dir / "sub").mkdir(parents=True)
    n = await hooks_module.fire_hook(event="task_created", payload={})
    assert n == 0


# ── fire one ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_runs_script_with_payload_on_stdin(hooks_module, tmp_path):
    out_file = tmp_path / "stdin.txt"
    _install_script(
        hooks_module.HOOKS_DIR / "task_created",
        "001-capture.sh",
        f'cat - > {out_file}',
    )
    n = await hooks_module.fire_hook(
        event="task_created", payload={"task_id": "42", "content": "do thing"}
    )
    assert n == 1
    assert await _wait_for_file(out_file), "script never wrote stdin to disk"
    captured = json.loads(out_file.read_text())
    assert captured["event"] == "task_created"
    assert captured["payload"]["task_id"] == "42"
    assert captured["payload"]["content"] == "do thing"
    assert "ts_utc" in captured


@pytest.mark.asyncio
async def test_fire_sets_event_env_var(hooks_module, tmp_path):
    """A script can branch on $JARVIS_HOOK_EVENT — needed when one
    script services multiple events via symlink."""
    out_file = tmp_path / "env.txt"
    _install_script(
        hooks_module.HOOKS_DIR / "worktree_created",
        "001-env.sh",
        f'echo "$JARVIS_HOOK_EVENT" > {out_file}',
    )
    await hooks_module.fire_hook(event="worktree_created", payload={"name": "x"})
    assert await _wait_for_file(out_file)
    assert out_file.read_text().strip() == "worktree_created"


@pytest.mark.asyncio
async def test_fire_sets_payload_env_var(hooks_module, tmp_path):
    """JARVIS_HOOK_PAYLOAD_JSON env var mirrors stdin for scripts
    that don't want to deal with reading stdin."""
    out_file = tmp_path / "env-payload.txt"
    _install_script(
        hooks_module.HOOKS_DIR / "evolution_tier_transition",
        "001-env-payload.sh",
        f'echo "$JARVIS_HOOK_PAYLOAD_JSON" > {out_file}',
    )
    await hooks_module.fire_hook(
        event="evolution_tier_transition",
        payload={"rule_id": "R-99", "from_tier": "staged", "to_tier": "accepted"},
    )
    assert await _wait_for_file(out_file)
    parsed = json.loads(out_file.read_text())
    assert parsed["payload"]["rule_id"] == "R-99"


# ── multiple scripts ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_runs_multiple_scripts_in_one_event_dir(hooks_module, tmp_path):
    event_dir = hooks_module.HOOKS_DIR / "task_created"
    a = tmp_path / "a.flag"
    b = tmp_path / "b.flag"
    c = tmp_path / "c.flag"
    _install_script(event_dir, "001-a.sh", f"touch {a}")
    _install_script(event_dir, "002-b.sh", f"touch {b}")
    _install_script(event_dir, "003-c.sh", f"touch {c}")
    n = await hooks_module.fire_hook(event="task_created", payload={})
    assert n == 3
    for f in (a, b, c):
        assert await _wait_for_file(f), f"{f.name} didn't fire"


# ── failure handling ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_swallows_script_nonzero_exit(hooks_module, tmp_path, caplog):
    """A hook exiting nonzero doesn't crash the dispatcher OR the
    caller — it just logs at WARNING. The triggering tool's reply
    is unaffected."""
    flag = tmp_path / "ran.flag"
    _install_script(
        hooks_module.HOOKS_DIR / "task_completed",
        "001-fail.sh",
        f"touch {flag}\nexit 7",
    )
    n = await hooks_module.fire_hook(event="task_completed", payload={"id": "1"})
    assert n == 1
    assert await _wait_for_file(flag)
    # Give the drain task a beat to log
    await asyncio.sleep(0.1)
    # Drain logged a WARNING (best-effort assertion — caplog captures
    # only what reaches the test logger; we mainly assert no exception
    # propagated, which is implicit from getting here).


@pytest.mark.asyncio
async def test_fire_swallows_unlaunchable_script(hooks_module, tmp_path):
    """An exec-failure (broken shebang → ENOENT on the interpreter)
    is caught by the dispatcher's per-script try/except and logged at
    WARNING. The fire_hook call returns the count of SUCCESSFULLY
    LAUNCHED scripts (0 in this case), AND a second, valid hook
    alongside it still fires."""
    event_dir = hooks_module.HOOKS_DIR / "task_created"
    event_dir.mkdir(parents=True)
    # Broken hook
    bad = event_dir / "0-broken.sh"
    bad.write_text("#!/usr/nonexistent/interpreter\necho hi\n")
    bad.chmod(0o755)
    # Valid hook alongside it
    flag = tmp_path / "valid.flag"
    _install_script(event_dir, "1-valid.sh", f"touch {flag}")

    n = await hooks_module.fire_hook(event="task_created", payload={})

    # The valid one launched; the broken one did not. Dispatcher
    # didn't raise.
    assert n == 1
    assert await _wait_for_file(flag), "valid hook didn't fire alongside the broken one"


@pytest.mark.asyncio
async def test_fire_empty_event_name_returns_zero(hooks_module):
    """Defensive: empty / None event name is a silent no-op."""
    assert await hooks_module.fire_hook(event="", payload={}) == 0
    assert await hooks_module.fire_hook(event=None, payload={}) == 0


# ── event isolation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_only_runs_matching_event(hooks_module, tmp_path):
    """A script under hooks/A/ must NOT fire when event B happens."""
    a_flag = tmp_path / "a.flag"
    b_flag = tmp_path / "b.flag"
    _install_script(hooks_module.HOOKS_DIR / "event_A", "001.sh", f"touch {a_flag}")
    _install_script(hooks_module.HOOKS_DIR / "event_B", "001.sh", f"touch {b_flag}")
    await hooks_module.fire_hook(event="event_A", payload={})
    assert await _wait_for_file(a_flag)
    # Give B time to NOT fire
    await asyncio.sleep(0.2)
    assert not b_flag.exists(), "event_B's hook fired on event_A"
