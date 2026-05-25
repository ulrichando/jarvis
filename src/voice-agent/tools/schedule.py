"""Schedule Tool — manage JARVIS cron jobs from the voice agent.

Registered tool name: ``schedule``

Wraps ``pipeline/cron_jobs.py`` (JARVIS's own job store) to let the
supervisor create, list, pause/resume, and remove scheduled jobs.

Actions
-------
create  — schedule a new recurring or one-shot job
list    — show all jobs
pause   — disable a job by id
resume  — re-enable a job by id
remove  — delete a job by id
run_now — mark a job due immediately (trigger next tick)

Jobs persist in ``~/.jarvis/cron/jobs.json`` and are executed by the
``jarvis-cron-scheduler.service`` / ``jarvis-cron.timer`` systemd units
(or whatever scheduler ticks ``pipeline.cron_scheduler``).

Zero external dependencies; all I/O via pipeline.cron_jobs stdlib helpers.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .registry import registry, tool_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cron_jobs():
    """Lazy import so the module loads cleanly even if pipeline isn't on path."""
    from pipeline import cron_jobs as _cj
    return _cj


def _format_job(job: dict) -> dict:
    """Compact representation for list/create responses."""
    sched = job.get("schedule") or {}
    kind = sched.get("kind", "?")
    if kind == "interval":
        sched_display = f"every {sched.get('every_s', '?')}s"
    elif kind == "daily-at":
        sched_display = f"daily at {sched.get('at', '?')}"
    elif kind == "once":
        sched_display = f"once at {sched.get('run_at', '?')}"
    else:
        sched_display = kind
    return {
        "id": job.get("id", "?"),
        "name": job.get("name", "?"),
        "type": job.get("type", "?"),
        "enabled": job.get("enabled", False),
        "pending_confirm": job.get("pending_confirm", False),
        "schedule": sched_display,
        "next_run_at": job.get("next_run_at"),
        "last_run_at": job.get("last_run_at"),
        "run_count": job.get("run_count", 0),
        "delivery": job.get("delivery", "notify+voice"),
        "prompt": (job.get("prompt") or "")[:120] or None,
        "command": (job.get("command") or "")[:120] or None,
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handle_schedule(args: dict, **_kw) -> str:
    cj = _cron_jobs()
    action = str(args.get("action", "")).strip().lower()

    if action == "create":
        name = str(args.get("name") or "").strip()
        schedule_text = str(args.get("schedule") or "").strip()
        job_type = str(args.get("type") or "prompt").strip().lower()
        prompt = str(args.get("prompt") or "").strip() or None
        command = str(args.get("command") or "").strip() or None
        delivery = str(args.get("delivery") or "notify+voice").strip()

        if not name:
            return tool_error("name is required for create")
        if not schedule_text:
            return tool_error("schedule is required for create — e.g. 'every 30m', 'daily at 09:00', 'in 2h'")
        if job_type not in ("script", "prompt"):
            return tool_error("type must be 'script' or 'prompt'")
        if job_type == "prompt" and not prompt:
            return tool_error("prompt is required when type='prompt'")
        if job_type == "script" and not command:
            return tool_error("command is required when type='script'")

        try:
            schedule = cj.parse_schedule(schedule_text)
        except ValueError as e:
            return tool_error(str(e))

        try:
            job = cj.new_job(
                name=name,
                type=job_type,
                schedule=schedule,
                command=command,
                prompt=prompt,
                delivery=delivery,
                created_by="voice",
            )
            added = cj.add_job(job)
        except ValueError as e:
            return tool_error(str(e))
        except Exception as e:
            logger.error("schedule create failed: %s", e, exc_info=True)
            return tool_error(f"Failed to create job: {e}")

        return json.dumps({
            "success": True,
            "job": _format_job(added),
            "message": (
                f"Job '{name}' created (id: {added['id']}). "
                "It will run after you confirm — say 'confirm schedule' or "
                "use the dashboard to enable it."
                if added.get("pending_confirm") else
                f"Job '{name}' created and scheduled (id: {added['id']})."
            ),
        }, ensure_ascii=False)

    if action == "list":
        jobs = cj.load_jobs()
        return json.dumps({
            "success": True,
            "count": len(jobs),
            "jobs": [_format_job(j) for j in jobs],
        }, ensure_ascii=False)

    # Actions that need a job_id
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return tool_error(f"job_id is required for action '{action}'")

    job = cj.get_job(job_id)
    if not job:
        return tool_error(f"Job '{job_id}' not found. Use action='list' to see job ids.")

    if action == "remove":
        ok = cj.remove_job(job_id)
        if not ok:
            return tool_error(f"Failed to remove job '{job_id}'")
        return json.dumps({"success": True, "message": f"Job '{job['name']}' removed."}, ensure_ascii=False)

    if action == "pause":
        updated = cj.set_enabled(job_id, False)
        return json.dumps({"success": True, "job": _format_job(updated or job)}, ensure_ascii=False)

    if action == "resume":
        updated = cj.set_enabled(job_id, True)
        return json.dumps({"success": True, "job": _format_job(updated or job)}, ensure_ascii=False)

    if action == "run_now":
        # Force next_run_at to now so the scheduler picks it up next tick.
        from datetime import datetime
        updated = cj._mutate(job_id, next_run_at=datetime.now().astimezone().isoformat())
        return json.dumps({
            "success": True,
            "message": f"Job '{job['name']}' queued for immediate execution.",
            "job": _format_job(updated or job),
        }, ensure_ascii=False)

    return tool_error(
        f"Unknown action '{action}'. Valid: create, list, pause, resume, remove, run_now."
    )


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def _check_schedule_available() -> bool:
    """Schedule tool is available when the pipeline.cron_jobs module imports cleanly."""
    try:
        from pipeline import cron_jobs  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------

_SCHEDULE_SCHEMA = {
    "name": "schedule",
    "description": (
        "Create and manage JARVIS scheduled jobs. Jobs run autonomously at the "
        "specified time — prompts run through the AI, scripts run as shell commands.\n\n"
        "Actions: create, list, pause, resume, remove, run_now.\n\n"
        "Schedule examples: 'every 30m', 'every 2h', 'daily at 09:00', "
        "'at 8am', 'in 1h', ISO timestamp.\n\n"
        "New voice-created jobs start paused pending confirmation. "
        "Use action='list' before remove/pause/resume to find the job id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "pause", "resume", "remove", "run_now"],
                "description": "What to do.",
            },
            "name": {
                "type": "string",
                "description": "Human-friendly job name (required for create).",
            },
            "schedule": {
                "type": "string",
                "description": (
                    "When to run. Examples: 'every 30m', 'every 2h', "
                    "'daily at 09:00', 'at 8am', 'in 1h', '2026-05-21T09:00'. "
                    "Required for create."
                ),
            },
            "type": {
                "type": "string",
                "enum": ["prompt", "script"],
                "description": (
                    "'prompt': AI runs the prompt each tick (default). "
                    "'script': shell command runs verbatim."
                ),
            },
            "prompt": {
                "type": "string",
                "description": "Self-contained task prompt (required when type='prompt').",
            },
            "command": {
                "type": "string",
                "description": "Shell command to run (required when type='script').",
            },
            "delivery": {
                "type": "string",
                "enum": ["notify+voice", "notify", "voice", "local"],
                "description": (
                    "How to deliver results. 'notify+voice' (default): system "
                    "notification + voice readout. 'notify': notification only. "
                    "'voice': voice only. 'local': save locally, no delivery."
                ),
            },
            "job_id": {
                "type": "string",
                "description": "Job id (required for pause/resume/remove/run_now). Use list to find ids.",
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="schedule",
    schema=_SCHEDULE_SCHEMA,
    handler=_handle_schedule,
    toolset="builtin",
    check_fn=_check_schedule_available,
    description=(
        "Create and manage JARVIS scheduled jobs — recurring reminders, daily "
        "prompts, or script-based automations."
    ),
    emoji="⏰",
)
