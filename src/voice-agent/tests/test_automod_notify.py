"""Tests for proposal-ready notification (sub-project C, 2026-06-23)."""
from __future__ import annotations

import subprocess

import pytest

from pipeline.automod import notify


@pytest.fixture(autouse=True)
def _hermetic_home(monkeypatch, tmp_path):
    """Isolate the evolution pause flag from the real ~/.jarvis. Without this, a
    paused dev box makes the pause gate return False and the notifier-fires
    assertions below fail (regression caught 2026-06-28)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))


def _exercise_real_dispatch(monkeypatch):
    """Drop the pytest-suppression env so a test can exercise the REAL notify
    dispatch. notify_proposal_ready no-ops under PYTEST_CURRENT_TEST so the rest
    of the suite never fires a real desktop popup — but the tests that verify the
    dispatch itself must opt back in. Done in the body (not a fixture) because
    pytest re-sets PYTEST_CURRENT_TEST at the start of the call phase."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


def test_notify_returns_false_when_no_notifier(monkeypatch):
    _exercise_real_dispatch(monkeypatch)
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    assert notify.notify_proposal_ready("automod-x", "fix latency") is False


def test_notify_invokes_notifier_with_intent(monkeypatch):
    _exercise_real_dispatch(monkeypatch)
    calls = {}
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    assert notify.notify_proposal_ready("automod-2026-06-23-abc", "tighten latency") is True
    assert calls["cmd"][0] == "/usr/bin/notify-send"
    joined = " ".join(calls["cmd"])
    assert "tighten latency" in joined
    assert "automod-2026-06-23-abc" in joined


def test_notify_never_raises(monkeypatch):
    _exercise_real_dispatch(monkeypatch)
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")

    def boom(*a, **k):
        raise OSError("dbus down")

    monkeypatch.setattr(notify.subprocess, "run", boom)
    assert notify.notify_proposal_ready("automod-x", "x") is False  # swallowed


def test_notify_suppressed_when_paused(monkeypatch):
    """A paused evolution cycle must not fire desktop notifications. Closes the
    'pause didn't stop the popups' bug (2026-06-28)."""
    _exercise_real_dispatch(monkeypatch)
    from pipeline.automod._state import set_evolution_paused

    set_evolution_paused(True)  # writes the flag under the hermetic JARVIS_HOME
    invoked = {"n": 0}
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr(
        notify.subprocess,
        "run",
        lambda *a, **k: invoked.__setitem__("n", invoked["n"] + 1),
    )
    assert notify.notify_proposal_ready("automod-x", "x") is False
    assert invoked["n"] == 0  # notifier never invoked when paused


def test_notify_suppressed_under_pytest(monkeypatch):
    """The suite must never fire a REAL notification: with PYTEST_CURRENT_TEST
    set (the normal in-test state), notify is a no-op even with a notifier on
    PATH. This is what stops the automod finalize/cycle tests from spamming the
    desktop (and the phone) with throwaway proposals (2026-06-28)."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::y (call)")
    invoked = {"n": 0}
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr(
        notify.subprocess,
        "run",
        lambda *a, **k: invoked.__setitem__("n", invoked["n"] + 1),
    )
    assert notify.notify_proposal_ready("automod-x", "x") is False
    assert invoked["n"] == 0  # notifier never invoked under pytest
