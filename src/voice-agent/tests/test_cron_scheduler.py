import asyncio
import sqlite3
from datetime import datetime, timedelta
from pipeline import cron_scheduler as cs
from pipeline import cron_jobs as cj


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(cj, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cj, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cj, "OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr(cj, "PENDING_FILE", tmp_path / "cron" / "pending.jsonl")
    monkeypatch.setattr(cs, "AUDIT_DB", tmp_path / "telemetry.db")


def test_run_script_job_delivers_stdout(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    delivered = []
    monkeypatch.setattr(cs, "_deliver", lambda job, text: delivered.append(text))
    job = cj.new_job(name="echo", type="script", command="echo hello-world",
                     schedule={"kind": "interval", "every_s": 60}, created_by="config")
    ok, out = asyncio.run(cs.run_job(job))
    assert ok and "hello-world" in out
    assert delivered == ["hello-world"]


def test_silent_job_suppresses_delivery(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    delivered = []
    monkeypatch.setattr(cs, "_deliver", lambda job, text: delivered.append(text))
    job = cj.new_job(name="quiet", type="script", command="echo '[SILENT]'",
                     schedule={"kind": "interval", "every_s": 60}, created_by="config")
    ok, out = asyncio.run(cs.run_job(job))
    assert ok and delivered == []


def test_run_prompt_job_uses_llm(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    delivered = []
    monkeypatch.setattr(cs, "_deliver", lambda job, text: delivered.append(text))

    async def fake_llm(prompt):
        return "Your day looks clear."
    monkeypatch.setattr(cs, "_call_job_llm", fake_llm)
    job = cj.new_job(name="brief", type="prompt", prompt="brief me",
                     schedule={"kind": "interval", "every_s": 60}, created_by="config")
    ok, out = asyncio.run(cs.run_job(job))
    assert ok and delivered == ["Your day looks clear."]


def test_run_job_audited(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    monkeypatch.setattr(cs, "_deliver", lambda job, text: None)
    job = cj.new_job(name="echo", type="script", command="echo hi",
                     schedule={"kind": "interval", "every_s": 60}, created_by="config")
    asyncio.run(cs.run_job(job))
    rows = sqlite3.connect(cs.AUDIT_DB).execute("SELECT job_id, ok FROM cron_runs").fetchall()
    assert rows and rows[0][1] == 1


def test_failed_script_marks_not_ok(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    monkeypatch.setattr(cs, "_deliver", lambda job, text: None)
    job = cj.new_job(name="fail", type="script", command="exit 1",
                     schedule={"kind": "interval", "every_s": 60}, created_by="config")
    ok, out = asyncio.run(cs.run_job(job))
    assert not ok


def test_tick_runs_due_and_advances_first(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    ran = []

    async def fake_run_job(job):
        ran.append(job["id"])
        return True, "ok"
    monkeypatch.setattr(cs, "run_job", fake_run_job)

    now = datetime(2026, 5, 20, 8, 0, 0).astimezone()
    j = cj.add_job(cj.new_job(name="r", type="script", command="x",
                              schedule={"kind": "interval", "every_s": 60}, created_by="config"))
    cj._mutate(j["id"], next_run_at=(now - timedelta(seconds=5)).isoformat())

    asyncio.run(cs.tick(_now=now))
    # advanced before run -> next_run_at is in the future
    assert datetime.fromisoformat(cj.get_job(j["id"])["next_run_at"]) > now
    assert ran == [j["id"]]
