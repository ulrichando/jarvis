"""Tests for proposal-ready notification (sub-project C, 2026-06-23)."""
from __future__ import annotations

import subprocess

from pipeline.automod import notify


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
