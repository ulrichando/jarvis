"""Supervisor tools for the between-turn scheduler. Voice-created jobs are
staged pending_confirm and only run after confirm_schedule — the supervisor
MUST read the summary back and get a verbal yes first. @function_tool shape
matches tools/skill_runner.py."""
from __future__ import annotations

import logging

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.schedule_tool")


@function_tool
async def schedule(when: str, what: str, kind: str = "script") -> str:
    """Create a scheduled job (STAGED — not active until you confirm it aloud).

    Use when the user asks JARVIS to do something on a schedule, e.g.
    "every morning at 8 run my repo summary" or "remind me in 2 hours".

    Args:
        when: schedule phrase — 'every 30m', 'daily at 08:00', 'in 2h', or an
              ISO timestamp.
        what: a shell command (kind='script') or an instruction (kind='prompt').
        kind: 'script' (runs a command, speaks its output) or 'prompt' (asks
              the LLM, speaks the answer — text-only, no tools in this version).

    Returns a confirmation summary you MUST read back to the user. Only call
    confirm_schedule(job_id) after they say yes.
    """
    from pipeline import cron_jobs as cj
    try:
        sched = cj.parse_schedule(when)
        job = cj.new_job(
            name=what[:50], type=kind,
            command=what if kind == "script" else None,
            prompt=what if kind == "prompt" else None,
            schedule=sched, created_by="voice")
        cj.add_job(job)
    except ValueError as e:
        return f"Couldn't schedule that: {e}"
    return (f"Staged job {job['id'][:6]} — {kind} '{what[:60]}' {when}. "
            f"It will notify you and speak the result. Say yes to confirm, "
            f"then I'll call confirm_schedule('{job['id']}').")


@function_tool
async def confirm_schedule(job_id: str) -> str:
    """Activate a job staged by schedule(). Call only after the user agrees."""
    from pipeline import cron_jobs as cj
    j = cj.set_confirmed(job_id)
    return f"Scheduled — '{j['name']}' is now active." if j else f"No staged job {job_id}."


@function_tool
async def list_schedules() -> str:
    """List the user's scheduled jobs (id, name, schedule, state)."""
    from pipeline import cron_jobs as cj
    jobs = cj.load_jobs()
    if not jobs:
        return "No scheduled jobs."
    lines = [f"{len(jobs)} job(s):"]
    for j in jobs:
        state = "pending-confirm" if j.get("pending_confirm") else ("on" if j.get("enabled") else "off")
        lines.append(f"  • {j['id'][:6]} {j['name']} — {j['schedule'].get('kind')} [{state}]")
    return "\n".join(lines)


@function_tool
async def cancel_schedule(job_id: str) -> str:
    """Delete a scheduled job by its id (the 6-char prefix from list works too)."""
    from pipeline import cron_jobs as cj
    full = next((j["id"] for j in cj.load_jobs() if j["id"].startswith(job_id)), None)
    if full and cj.remove_job(full):
        return f"Cancelled job {job_id}."
    return f"No job matching {job_id}."
