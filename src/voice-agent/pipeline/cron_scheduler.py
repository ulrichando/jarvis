"""Scheduler execution + tick loop (phase 1).

Job types:
  - script: run command via subprocess, deliver stdout.
  - prompt: text-only LLM call (no tool loop in phase 1), deliver text.

A leading '[SILENT]' in output suppresses delivery. Every run is audited
to the cron_runs table in turn_telemetry.db. Live-session speaking is wired
by jarvis_agent.py via set_live_say(); when absent, voice falls back to the
pending queue.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable

from pipeline import cron_jobs as cj
from pipeline import cron_delivery as cd

logger = logging.getLogger("jarvis.cron_scheduler")

AUDIT_DB = Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db"
SCRIPT_TIMEOUT_S = int(os.environ.get("JARVIS_CRON_SCRIPT_TIMEOUT", "120"))

# Set by jarvis_agent when a voice room is live: async fn(text)->None.
_live_say: Callable | None = None


def set_live_say(fn: Callable | None) -> None:
    global _live_say
    _live_say = fn


def _record_run(job_id: str, jtype: str, ok: bool, dur_ms: int, delivered: bool) -> None:
    try:
        AUDIT_DB.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(AUDIT_DB)) as con:
            con.execute("CREATE TABLE IF NOT EXISTS cron_runs ("
                        "job_id TEXT, ts_utc REAL, type TEXT, ok INTEGER, "
                        "duration_ms INTEGER, delivered INTEGER)")
            con.execute("INSERT INTO cron_runs VALUES (?,?,?,?,?,?)",
                        (job_id, time.time(), jtype, int(ok), dur_ms, int(delivered)))
            con.commit()
    except Exception as e:  # pragma: no cover - audit must never break a run
        logger.warning("[cron] audit write failed: %s", e)


async def _call_job_llm(prompt: str) -> str:
    """Text-only Groq call for prompt jobs. Mirrors
    pipeline/memory_extractor.py::_call_extractor_llm. Monkeypatched in tests."""
    import httpx
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return "[SILENT]"
    model = os.environ.get("JARVIS_CRON_PROMPT_MODEL", "llama-3.3-70b-versatile")
    sys = ("You are JARVIS running a scheduled background task with no tools. "
           "Produce a concise spoken-style result. If there is nothing useful "
           "to report, reply exactly [SILENT].")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "temperature": 0.3, "max_tokens": 400,
                      "messages": [{"role": "system", "content": sys},
                                   {"role": "user", "content": prompt}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("[cron] prompt-job LLM failed: %s", e)
            return f"Job failed: {type(e).__name__}"


async def _run_script(command: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=SCRIPT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()  # reap the killed process (avoid zombie on the long-lived daemon)
        return 124, "script timed out"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


def _deliver(job: dict, text: str) -> None:
    """Route output per job['delivery']. Voice -> live say if connected, else queue."""
    mode = job.get("delivery", "notify+voice")
    if "notify" in mode:
        cd.notify("JARVIS", f"{job['name']}: {text}")
    if "voice" in mode:
        if _live_say is not None:
            asyncio.create_task(_live_say(f"{job['name']}: {text}"))
        else:
            cd.queue_pending(job["name"], text)


async def run_job(job: dict) -> tuple[bool, str]:
    """Execute one job, deliver (unless [SILENT]), audit. Returns (ok, output)."""
    start = time.time()
    try:
        if job["type"] == "script":
            rc, out = await _run_script(job["command"])
            ok = rc == 0
        else:
            out = await _call_job_llm(job["prompt"])
            ok = not out.startswith("Job failed:")
        silent = out.strip().startswith("[SILENT]")
        delivered = ok and not silent and bool(out)
        if delivered:
            _deliver(job, out)
        try:
            cj.ensure_dirs()
            d = cj.OUTPUT_DIR / job["id"]
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{int(start)}.md").write_text(out, encoding="utf-8")
        except Exception:
            pass
        _record_run(job["id"], job["type"], ok, int((time.time() - start) * 1000), delivered)
        return ok, out
    except Exception as e:
        logger.warning("[cron] run_job %s crashed: %s", job.get("id"), e)
        _record_run(job.get("id", "?"), job.get("type", "?"), False,
                    int((time.time() - start) * 1000), False)
        return False, f"Job failed: {e}"


try:
    import fcntl
except ImportError:  # Windows has no fcntl — the tick overlap-guard is skipped
    fcntl = None

TICK_INTERVAL_S = int(os.environ.get("JARVIS_CRON_TICK_S", "60"))
_LOCK_PATH = cj.CRON_DIR / ".tick.lock"


async def tick(*, _now=None) -> None:
    """Select due jobs, advance recurring ones first (at-most-once), run them
    off-band, then mark each run's outcome."""
    now_ = _now or cj.now()
    for job in cj.get_due_jobs(_now=now_):
        cj.advance_next_run(job["id"], _now=now_)
        ok, _out = await run_job(job)
        cj.mark_job_run(job["id"], ok=ok, _now=now_)


async def run_forever() -> None:
    """60s tick loop for the daemon. Gated by JARVIS_CRON_DISABLED; an fcntl
    lock prevents overlap if a tick overruns. Never raises out of the loop."""
    if os.environ.get("JARVIS_CRON_DISABLED") == "1":
        logger.info("[cron] scheduler disabled via JARVIS_CRON_DISABLED=1")
        return
    cj.ensure_dirs()
    logger.info("[cron] scheduler started (tick=%ss)", TICK_INTERVAL_S)
    while True:
        try:
            if fcntl is None:
                # Windows: single in-process scheduler, no systemd timer to
                # overlap with, so the cross-process tick lock isn't needed.
                await tick()
            else:
                with open(_LOCK_PATH, "w", encoding="utf-8") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        await tick()
                    except BlockingIOError:
                        logger.warning("[cron] previous tick still running; skipping")
        except Exception as e:
            logger.warning("[cron] tick error: %s", e)
        await asyncio.sleep(TICK_INTERVAL_S)
