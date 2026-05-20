import asyncio
from tools import schedule as sch
from pipeline import cron_jobs as cj


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(cj, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cj, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cj, "OUTPUT_DIR", tmp_path / "cron" / "output")


def _call(tool, **kw):
    # livekit @function_tool wraps the coroutine; resolve the raw async fn.
    fn = getattr(tool, "__wrapped__", tool)
    return asyncio.run(fn(**kw))


def test_schedule_stages_pending_and_reads_back(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    out = _call(sch.schedule, when="every 30m", what="echo hi", kind="script")
    assert "confirm" in out.lower()
    jobs = cj.load_jobs()
    assert len(jobs) == 1 and jobs[0]["pending_confirm"] is True


def test_confirm_enables(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    _call(sch.schedule, when="every 30m", what="echo hi", kind="script")
    jid = cj.load_jobs()[0]["id"]
    _call(sch.confirm_schedule, job_id=jid)
    assert cj.get_job(jid)["enabled"] is True


def test_list_and_cancel(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    _call(sch.schedule, when="every 30m", what="echo hi", kind="script")
    jid = cj.load_jobs()[0]["id"]
    assert jid[:6] in _call(sch.list_schedules)
    _call(sch.cancel_schedule, job_id=jid)
    assert cj.get_job(jid) is None
