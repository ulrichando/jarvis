from pipeline import cron_delivery as cd
from pipeline import cron_jobs as cj


def test_notify_invokes_notify_send(monkeypatch):
    calls = []
    monkeypatch.setattr(cd.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    monkeypatch.setattr(cd.shutil, "which", lambda _: "/usr/bin/notify-send")
    cd.notify("JARVIS", "morning brief ready")
    assert calls and calls[0][0] == "notify-send"


def test_notify_graceful_without_binary(monkeypatch):
    monkeypatch.setattr(cd.shutil, "which", lambda _: None)
    cd.notify("JARVIS", "x")  # must not raise


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
