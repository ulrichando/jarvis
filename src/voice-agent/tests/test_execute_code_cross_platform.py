"""Cross-platform regression tests for tools.execute_code._kill_process_group.

Phase 2.1 swapped the previous POSIX-only ``os.killpg`` /
``signal.SIGKILL`` implementation for a ``psutil``-based one so the same
code path runs on Windows (where neither ``os.killpg`` nor
``signal.SIGKILL`` exist). These tests pin the behavior with mocked
``psutil.Process`` so we don't actually spawn or kill anything.
"""
from __future__ import annotations

import subprocess
from unittest import mock

import psutil
import pytest


def _fake_proc(pid: int = 12345) -> subprocess.Popen:
    """Return a Popen-shaped Mock that satisfies type and pid access."""
    p = mock.MagicMock(spec=subprocess.Popen)
    p.pid = pid
    return p


class _FakePsProcess:
    """Minimal psutil.Process stand-in with the methods _kill_process_group uses."""

    def __init__(self, pid: int, children: list | None = None, alive: bool = True):
        self.pid = pid
        self._children = children or []
        self._alive = alive
        self.terminate = mock.Mock()
        self.kill = mock.Mock()

    def children(self, recursive: bool = False):
        return list(self._children)

    def is_running(self) -> bool:
        return self._alive


def test_kill_process_group_terminates_parent_only_when_no_children():
    from tools import execute_code as ec

    proc = _fake_proc()
    fake_parent = _FakePsProcess(pid=proc.pid, children=[])

    with mock.patch.object(ec.psutil, "Process", return_value=fake_parent):
        ec._kill_process_group(proc, escalate=False)

    fake_parent.terminate.assert_called_once()
    fake_parent.kill.assert_not_called()


def test_kill_process_group_terminates_children_then_parent():
    """Deepest-first termination — descendants snapshot before parent dies."""
    from tools import execute_code as ec

    proc = _fake_proc()
    child_a = _FakePsProcess(pid=99001)
    child_b = _FakePsProcess(pid=99002)
    fake_parent = _FakePsProcess(pid=proc.pid, children=[child_a, child_b])

    with mock.patch.object(ec.psutil, "Process", return_value=fake_parent):
        ec._kill_process_group(proc, escalate=False)

    child_a.terminate.assert_called_once()
    child_b.terminate.assert_called_once()
    fake_parent.terminate.assert_called_once()
    # No escalation requested, so no .kill() anywhere.
    fake_parent.kill.assert_not_called()
    child_a.kill.assert_not_called()
    child_b.kill.assert_not_called()


def test_kill_process_group_escalates_to_kill_on_timeout():
    """If proc.wait times out, the implementation should escalate to .kill()."""
    from tools import execute_code as ec

    proc = _fake_proc()
    # proc.wait raises TimeoutExpired → triggers the .kill() escalation path.
    proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=5)
    child = _FakePsProcess(pid=99001, alive=True)
    fake_parent = _FakePsProcess(pid=proc.pid, children=[child], alive=True)

    with mock.patch.object(ec.psutil, "Process", return_value=fake_parent):
        ec._kill_process_group(proc, escalate=True)

    # First wave: terminate.
    fake_parent.terminate.assert_called_once()
    child.terminate.assert_called_once()
    # Escalation wave: kill (because is_running returned True).
    fake_parent.kill.assert_called_once()
    child.kill.assert_called_once()


def test_kill_process_group_handles_already_dead_process():
    """A stale PID (process already gone) is a no-op, not a crash."""
    from tools import execute_code as ec

    proc = _fake_proc()
    with mock.patch.object(
        ec.psutil, "Process", side_effect=psutil.NoSuchProcess(pid=proc.pid)
    ):
        # Must not raise.
        ec._kill_process_group(proc, escalate=False)


def test_kill_process_group_handles_child_disappearing_mid_kill():
    """Children that exit between snapshot and .terminate() shouldn't crash."""
    from tools import execute_code as ec

    proc = _fake_proc()
    dying_child = _FakePsProcess(pid=99001)
    dying_child.terminate.side_effect = psutil.NoSuchProcess(pid=99001)
    fake_parent = _FakePsProcess(pid=proc.pid, children=[dying_child])

    with mock.patch.object(ec.psutil, "Process", return_value=fake_parent):
        # Must not raise.
        ec._kill_process_group(proc, escalate=False)

    # Parent still gets the terminate signal.
    fake_parent.terminate.assert_called_once()


def test_kill_process_group_falls_back_to_proc_kill_on_unexpected_error():
    """An unexpected psutil error should still try to kill the bare proc."""
    from tools import execute_code as ec

    proc = _fake_proc()
    # Force an unexpected exception (something other than NoSuchProcess).
    with mock.patch.object(ec.psutil, "Process", side_effect=RuntimeError("boom")):
        ec._kill_process_group(proc, escalate=False)

    proc.kill.assert_called_once()
