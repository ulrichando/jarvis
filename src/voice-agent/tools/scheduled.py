"""``scheduled`` voice tool — list and run the user's Home scheduled tasks (the
web app's Scheduled feature) and read the result aloud.

Distinct from ``web_routine`` (the /code-shell routines). These are the Home
"Scheduled" tasks — a prompt that runs on a cron and lands as a chat.

Queries EVERY configured instance (``JARVIS_SCHEDULED_WEB_URLS``, comma-
separated; default the VPS ``https://0wlan.com`` plus the local web
``http://127.0.0.1:3000``) with the shared token ``JARVIS_SCHEDULED_VOICE_TOKEN``.
A scheduled brief can only use the data tools installed on the instance it runs
on (e.g. the Coding-Kiddos MCP lives on the local box, GitHub on the VPS), so
each task is listed with its instance and RUN on the instance it belongs to.

Gated (``check_fn``) on the token being configured.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .registry import registry, tool_error

_DEFAULT_URLS = "https://0wlan.com,http://127.0.0.1:3000"
_TIMEOUT_S = 30.0  # a run generates an LLM brief with tool calls; give it room


def _token() -> str:
    return os.environ.get("JARVIS_SCHEDULED_VOICE_TOKEN", "").strip()


def _configured() -> bool:
    return bool(_token())


def _instances() -> List[str]:
    # Back-compat: honor the singular var, else the plural list, else defaults.
    raw = (
        os.environ.get("JARVIS_SCHEDULED_WEB_URLS")
        or os.environ.get("JARVIS_SCHEDULED_WEB_URL")
        or _DEFAULT_URLS
    )
    seen, out = set(), []
    for u in raw.split(","):
        u = u.strip().rstrip("/")
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _label(url: str) -> str:
    return "local" if ("127.0.0.1" in url or "localhost" in url) else "cloud"


async def _get(url: str, path: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
        r = await c.get(
            f"{url}{path}", headers={"Authorization": f"Bearer {_token()}"}
        )
        r.raise_for_status()
        return r.json()


async def _post(url: str, path: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
        r = await c.post(
            f"{url}{path}", headers={"Authorization": f"Bearer {_token()}"}
        )
        r.raise_for_status()
        return r.json()


async def _all_tasks() -> List[Tuple[str, Dict[str, Any]]]:
    """(instance_url, task) across every reachable instance. Unreachable
    instances are skipped (a laptop offline / VPS blip must not error the whole
    list)."""
    async def one(url: str) -> List[Tuple[str, Dict[str, Any]]]:
        try:
            tasks = (await _get(url, "/api/scheduled")).get("tasks", [])
            return [(url, t) for t in tasks]
        except Exception:  # noqa: BLE001 — skip an unreachable instance
            return []

    results = await asyncio.gather(*(one(u) for u in _instances()))
    return [pair for group in results for pair in group]


def _fmt_list(pairs: List[Tuple[str, Dict[str, Any]]]) -> str:
    if not pairs:
        return "You don't have any scheduled tasks set up."
    multi = len({_label(u) for u, _ in pairs}) > 1
    lines = []
    for url, t in pairs:
        state = "paused" if t.get("paused") else (t.get("label") or t.get("cron", ""))
        where = f" [{_label(url)}]" if multi else ""
        lines.append(f"- {t.get('name', 'Untitled')} ({state}){where}")
    return "Your scheduled tasks:\n" + "\n".join(lines)


def _match(
    pairs: List[Tuple[str, Dict[str, Any]]], name: str
) -> Optional[Tuple[str, Dict[str, Any]]]:
    q = name.strip().lower()
    if not q:
        return None
    for url, t in pairs:  # exact first
        if (t.get("name") or "").lower() == q:
            return (url, t)
    for url, t in pairs:  # then substring
        if q in (t.get("name") or "").lower():
            return (url, t)
    return None


async def _handle_scheduled(args: Dict[str, Any]) -> str:
    action = (args.get("action") or "list").strip().lower()
    try:
        pairs = await _all_tasks()
        if action == "list":
            return _fmt_list(pairs)
        if action == "run":
            name = args.get("name") or ""
            hit = _match(pairs, name)
            if not hit:
                avail = ", ".join(t.get("name", "?") for _, t in pairs) or "none"
                return f"I couldn't find a scheduled task called '{name}'. You have: {avail}."
            url, task = hit
            res = await _post(url, f"/api/scheduled/{task['id']}/run")
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
