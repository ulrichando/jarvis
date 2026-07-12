"""``scheduled`` voice tool — let JARVIS list and run the user's Home scheduled
tasks (the web app's Scheduled feature) and read the result aloud.

Distinct from ``web_routine`` (that's the /code-shell routines). These are the
Home "Scheduled" tasks — a prompt that runs on a cron and lands as a chat.

Unlike the other ``web_*`` tools, this one targets the user's canonical web
instance — the VPS by default (``JARVIS_SCHEDULED_WEB_URL``, default
``https://0wlan.com``) — and authenticates with the dedicated shared token
``JARVIS_SCHEDULED_VOICE_TOKEN`` (the same secret the reminder poller uses),
NOT the local-api token. So "run my morning brief" hits the always-on VPS
store where the user creates their tasks, and reads back the generated brief.

Gated (``check_fn``) on the token being configured, so the tool only appears
once the scheduled-voice bridge is set up.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from .registry import registry, tool_error

_DEFAULT_URL = "https://0wlan.com"
_TIMEOUT_S = 30.0  # a run generates an LLM brief; give it room


def _base_url() -> str:
    return os.environ.get("JARVIS_SCHEDULED_WEB_URL", _DEFAULT_URL).rstrip("/")


def _token() -> str:
    return os.environ.get("JARVIS_SCHEDULED_VOICE_TOKEN", "").strip()


def _configured() -> bool:
    return bool(_token())


async def _get(path: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
        r = await c.get(
            f"{_base_url()}{path}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        r.raise_for_status()
        return r.json()


async def _post(path: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
        r = await c.post(
            f"{_base_url()}{path}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        r.raise_for_status()
        return r.json()


def _fmt_list(tasks: List[Dict[str, Any]]) -> str:
    if not tasks:
        return "You don't have any scheduled tasks set up."
    lines = []
    for t in tasks:
        state = "paused" if t.get("paused") else (t.get("label") or t.get("cron", ""))
        lines.append(f"- {t.get('name', 'Untitled')} ({state})")
    return "Your scheduled tasks:\n" + "\n".join(lines)


def _match(tasks: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    q = name.strip().lower()
    if not q:
        return None
    # Exact (case-insensitive) first, then substring.
    for t in tasks:
        if (t.get("name") or "").lower() == q:
            return t
    for t in tasks:
        if q in (t.get("name") or "").lower():
            return t
    return None


async def _handle_scheduled(args: Dict[str, Any]) -> str:
    action = (args.get("action") or "list").strip().lower()
    try:
        tasks = (await _get("/api/scheduled")).get("tasks", [])
        if action == "list":
            return _fmt_list(tasks)
        if action == "run":
            name = args.get("name") or ""
            task = _match(tasks, name)
            if not task:
                avail = ", ".join(t.get("name", "?") for t in tasks) or "none"
                return f"I couldn't find a scheduled task called '{name}'. You have: {avail}."
            res = await _post(f"/api/scheduled/{task['id']}/run")
            text = (res.get("text") or "").strip()
            return text or f"Ran '{task.get('name')}', but it produced no output."
        return tool_error(f"Unknown action '{action}' (use 'list' or 'run').")
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            return tool_error("The scheduled-tasks token was rejected by the web app.")
        if code == 503:
            return tool_error("Scheduled tasks aren't configured on the web app yet.")
        return tool_error(f"The web app returned HTTP {code}.")
    except httpx.HTTPError as e:
        return tool_error(f"Couldn't reach the scheduled-tasks web app: {e}")


_SCHEMA = {
    "type": "function",
    "name": "scheduled",
    "description": (
        "List the user's Home scheduled tasks, or run one now and read its "
        "result aloud. Use for requests like 'what's scheduled', 'run my "
        "morning brief', 'give me my morning briefing'. These are the web "
        "app's Scheduled tasks (a prompt that runs on a cron) — NOT /code "
        "routines (use web_routine for those)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "run"],
                "description": "'list' the scheduled tasks, or 'run' one now.",
            },
            "name": {
                "type": "string",
                "description": "For 'run': the task name to run (e.g. 'Morning Brief').",
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="scheduled",
    schema=_SCHEMA,
    handler=_handle_scheduled,
    toolset="web",
    check_fn=_configured,
    is_async=True,
)
