"""Prompt for the ScheduleCronTool."""
from __future__ import annotations

DEFAULT_MAX_AGE_DAYS = 30

CRON_CREATE_TOOL_NAME = "CronCreate"
CRON_DELETE_TOOL_NAME = "CronDelete"
CRON_LIST_TOOL_NAME = "CronList"


def build_cron_create_description(durable_enabled: bool = False) -> str:
    if durable_enabled:
        return (
            "Schedule a prompt to run at a future time -- either recurring on a cron "
            "schedule, or once at a specific time. Pass durable: true to persist to "
            ".jarvis/scheduled_tasks.json; otherwise session-only."
        )
    return (
        "Schedule a prompt to run at a future time within this JARVIS session -- "
        "either recurring on a cron schedule, or once at a specific time."
    )


def build_cron_create_prompt(durable_enabled: bool = False) -> str:
    durability_section = (
        """## Session-only

Jobs live only in this JARVIS session -- nothing is written to disk, and the job is gone when JARVIS exits."""
    )

    return f"""Schedule a prompt to be enqueued at a future time. Use for both recurring schedules and one-shot reminders.

Uses standard 5-field cron in the user's local timezone: minute hour day-of-month month day-of-week. "0 9 * * *" means 9am local -- no timezone conversion needed.

## One-shot tasks (recurring: false)

For "remind me at X" or "at <time>, do Y" requests -- fire once then auto-delete.
Pin minute/hour/day-of-month/month to specific values.

## Recurring jobs (recurring: true, the default)

For "every N minutes" / "every hour" / "weekdays at 9am" requests.

{durability_section}

Recurring tasks auto-expire after {DEFAULT_MAX_AGE_DAYS} days.

Returns a job ID you can pass to {CRON_DELETE_TOOL_NAME}."""


CRON_DELETE_DESCRIPTION = "Cancel a scheduled cron job by ID"


def build_cron_delete_prompt(durable_enabled: bool = False) -> str:
    return (
        f"Cancel a cron job previously scheduled with {CRON_CREATE_TOOL_NAME}. "
        "Removes it from the in-memory session store."
    )


CRON_LIST_DESCRIPTION = "List scheduled cron jobs"


def build_cron_list_prompt(durable_enabled: bool = False) -> str:
    return f"List all cron jobs scheduled via {CRON_CREATE_TOOL_NAME} in this session."
