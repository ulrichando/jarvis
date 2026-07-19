import os

from pipeline import cron_delivery as cd
from pipeline import cron_jobs as cj


def test_notify_invokes_notify_send(monkeypatch):
    # This test exercises the REAL delivery path — opt out of the suite-wide
    # kill-switch (conftest sets it) and stub the subprocess instead.
    monkeypatch.delenv("JARVIS_NOTIFY_DISABLED", raising=False)
    calls = []
    monkeypatch.setattr(cd.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(cd.shutil, "which", lambda _: "/usr/bin/notify-send")
    cd.notify("JARVIS", "morning brief ready")
    assert calls and calls[0][0] == "notify-send"


def test_notify_graceful_without_binary(monkeypatch):
    monkeypatch.delenv("JARVIS_NOTIFY_DISABLED", raising=False)
    monkeypatch.setattr(cd.shutil, "which", lambda _: None)
    cd.notify("JARVIS", "x")  # must not raise


def test_notify_kill_switch_suppresses_subprocess(monkeypatch):
    # JARVIS_NOTIFY_DISABLED=1 must short-circuit BEFORE which()/subprocess —
    # the switch headless boxes and the hermetic suite rely on.
    monkeypatch.setenv("JARVIS_NOTIFY_DISABLED", "1")
    calls = []
    monkeypatch.setattr(cd.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(cd.shutil, "which", lambda _: "/usr/bin/notify-send")
    cd.notify("JARVIS", "should never reach the desktop")
    assert calls == []


def test_suite_runs_with_notifications_disabled():
    # Regression guard on the conftest wiring itself (2026-07-16→19 incident:
    # a test fired a REAL "local speech-to-text GPU error" desktop toast once
    # per suite run for 3 days, impersonating a live GPU failure). If this
    # fails, pytest_configure lost the JARVIS_NOTIFY_DISABLED setdefault and
    # any test that forgets to stub notify() can spam the desktop again.
    assert os.environ.get("JARVIS_NOTIFY_DISABLED") == "1"
    assert cd.notifications_disabled() is True


def test_agent_notify_error_respects_kill_switch(monkeypatch):
    # jarvis_agent._notify_error shells out to notify-send directly (its own
    # urgency/expiry flags) — it must consult the same kill-switch.
    import subprocess as _sp
    import jarvis_agent as ja

    monkeypatch.setenv("JARVIS_NOTIFY_DISABLED", "1")
    monkeypatch.setattr(ja, "_ERR_NOTIFY_TS", [0.0])  # defeat the throttle
    spawns = []
    monkeypatch.setattr(_sp, "Popen", lambda *a, **k: spawns.append(a))

    class _C:
        recoverable = True
        notify_title = "t"
        notify_body = "b"

    ja._notify_error(_C())
    assert spawns == []
    # And with the switch off, the same call DOES spawn (throttle reset again).
    monkeypatch.delenv("JARVIS_NOTIFY_DISABLED", raising=False)
    monkeypatch.setattr(ja, "_ERR_NOTIFY_TS", [0.0])
    ja._notify_error(_C())
    assert len(spawns) == 1 and spawns[0][0][0] == "notify-send"


def test_queue_and_drain(tmp_path, monkeypatch):
    monkeypatch.setattr(cj, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cj, "OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr(cj, "PENDING_FILE", tmp_path / "cron" / "pending.jsonl")
    (tmp_path / "cron").mkdir()
    cd.queue_pending("repos", "3 dirty repos")
    cd.queue_pending("disk", "92% full")
    digest = cd.drain_pending()
    assert "repos" in digest and "disk" in digest
    assert cd.drain_pending() == ""  # cleared after drain
    assert not cj.PENDING_FILE.exists() or cj.PENDING_FILE.read_text() == ""


def test_drain_caps_and_tails(tmp_path, monkeypatch):
    monkeypatch.setattr(cj, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cj, "OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr(cj, "PENDING_FILE", tmp_path / "cron" / "pending.jsonl")
    monkeypatch.setattr(cd, "MAX_DIGEST_ITEMS", 3)
    (tmp_path / "cron").mkdir()
    for i in range(6):
        cd.queue_pending(f"job{i}", f"result{i}")
    digest = cd.drain_pending()
    assert "(and 3 more)" in digest      # 6 queued, 3 shown
    assert "job5" in digest and "job3" in digest   # newest shown
    assert "job0" not in digest                     # oldest dropped
