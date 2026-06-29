"""Tests for proposal-ready notification (sub-project C, 2026-06-23)."""
from __future__ import annotations

import subprocess

import pytest

from pipeline.automod import notify


@pytest.fixture(autouse=True)
def _hermetic_home(monkeypatch, tmp_path):
    """Isolate the evolution pause flag from the real ~/.jarvis. Without this, a
    paused dev box makes notify_proposal_ready's pause gate return False and the
    notifier-fires assertions below fail (regression caught 2026-06-28)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))


def test_notify_returns_false_when_no_notifier(monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    assert notify.notify_proposal_ready("automod-x", "fix latency") is False


def test_notify_invokes_notifier_with_intent(monkeypatch):
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
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")

    def boom(*a, **k):
        raise OSError("dbus down")

    monkeypatch.setattr(notify.subprocess, "run", boom)
    assert notify.notify_proposal_ready("automod-x", "x") is False  # swallowed


def test_notify_suppressed_when_paused(monkeypatch):
    """A paused evolution cycle must not fire desktop notifications. Closes the
    'pause didn't stop the popups' bug (2026-06-28): the gate lives at this
    universal notification chokepoint."""
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
