"""web_routine — voice control over the JARVIS web app's routines.

Lets JARVIS list / run / create / pause / resume / delete the web app's
**routines** (automations, managed under the Code tab, backed by
``/api/bridge/v1/routines``) by voice — e.g. "list my routines", "run my
morning routine", "create a routine called Standup that summarizes my inbox",
"pause the Standup routine".

``run`` executes the routine now (returns the spawned session id) using owner
auth — no per-routine token needed. NOTE: the cron *scheduler* isn't live yet,
so a routine created with a schedule won't auto-fire; ``run`` is how it executes
today. The tool doesn't promise auto-scheduling.

Voice-natural addressing: run / pause / resume / delete accept a routine
``name`` (resolved to its id via the list) or an explicit ``id``; ambiguous
names are refused.

Auth: the shared ``JARVIS_LOCAL_API_TOKEN`` bearer via :mod:`tools._web_api`;
the routines routes resolve it to the box owner (``getUserIdOrSharedLocal``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import _web_api
from .registry import registry, tool_error

_BASE = "/api/bridge/v1/routines"


def _fmt_list(routines: List[Dict[str, Any]]) -> str:
    if not routines:
        return "You have no routines yet."
    parts = []
    for r in routines[:20]:
        nm = str(r.get("name") or "untitled")
        parts.append(f"{nm} (paused)" if r.get("paused") else nm)
    n = len(routines)
    shown = ", ".join(parts)
    more = f" (+{n - len(parts)} more)" if n > len(parts) else ""
    head = "1 routine" if n == 1 else f"{n} routines"
    return f"You have {head}: {shown}{more}."


async def _resolve_id(args: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    rid = (args.get("id") or "").strip() if isinstance(args.get("id"), str) else ""
    if rid:
        return rid, None
    name = (args.get("name") or "").strip() if isinstance(args.get("name"), str) else ""
    if not name:
        return None, "which routine? Give a name or id."
    data = await _web_api.call_web("GET", _BASE)
    rows = data.get("routines") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None, "couldn't read the routine list to resolve that name."
    matches = [r for r in rows if str(r.get("name") or "").strip().lower() == name.lower()]
    if not matches:
        return None, f"no routine named {name!r}."
    if len(matches) > 1:
        return None, f"more than one routine is named {name!r} — say the id instead."
    return str(matches[0].get("routine_id") or ""), None


async def _handle_web_routine(args: Dict[str, Any]) -> str:
    args = args or {}
    action = (args.get("action") or "").strip().lower()

    try:
        if action == "list":
            data = await _web_api.call_web("GET", _BASE)
            rows = data.get("routines", []) if isinstance(data, dict) else []
            return _fmt_list(rows)

        if action == "run":
            rid, err = await _resolve_id(args)
            if err:
                return tool_error(f"web_routine: {err}")
            data = await _web_api.call_web("POST", f"{_BASE}/{rid}/run", json_body={})
            sid = (data or {}).get("session_id") if isinstance(data, dict) else None
            nm = str(args.get("name") or rid)
            return f"Started routine {nm!r}." + (f" (session {sid})" if sid else "")

        if action == "create":
            name = (args.get("name") or "").strip()
            instructions = (args.get("instructions") or "").strip()
            if not name or not instructions:
                return tool_error(
                    "web_routine: create needs a name and instructions "
                    "(what the routine should do)."
                )
            cron = (args.get("cron") or "").strip() if isinstance(args.get("cron"), str) else ""
            trigger = {"type": "schedule", "cron": cron} if cron else {"type": "api"}
            body: Dict[str, Any] = {"name": name, "instructions": instructions, "trigger": trigger}
            await _web_api.call_web("POST", _BASE, json_body=body)
            sched = " (scheduled — note: auto-run isn't live yet, use 'run' to execute)" if cron else ""
            return f"Created routine {name!r}.{sched}"

        if action in ("pause", "resume"):
            rid, err = await _resolve_id(args)
            if err:
                return tool_error(f"web_routine: {err}")
            await _web_api.call_web(
                "PATCH", f"{_BASE}/{rid}", json_body={"paused": action == "pause"}
            )
            nm = str(args.get("name") or rid)
            return f"{'Paused' if action == 'pause' else 'Resumed'} routine {nm!r}."

        if action == "delete":
            rid, err = await _resolve_id(args)
            if err:
                return tool_error(f"web_routine: {err}")
            await _web_api.call_web("DELETE", f"{_BASE}/{rid}")
            nm = str(args.get("name") or rid)
            return f"Deleted routine {nm!r}."

        return tool_error(
            f"web_routine: unknown action {action!r} "
            "(expected list, run, create, pause, resume, or delete)."
        )
    except _web_api.WebError as e:
        return tool_error(f"web_routine: {e}")


_SCHEMA = {
    "name": "web_routine",
    "description": (
        "List, run, create, pause, resume, or delete the JARVIS web app's "
        "routines (automations). Use when the user asks to manage or trigger a "
        "routine by voice — e.g. 'list my routines', 'run my morning routine', "
        "'create a routine called Standup that summarizes my inbox', 'pause the "
        "Standup routine'. 'run' executes the routine now. Addresses routines by "
        "name (resolved automatically) or id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "run", "create", "pause", "resume", "delete"],
                "description": "What to do.",
            },
            "name": {
                "type": "string",
                "description": (
                    "For 'create': the new routine's name. For run/pause/resume/"
                    "delete: the routine to target (resolved to its id; ambiguous "
                    "names are rejected)."
                ),
            },
            "id": {
                "type": "string",
                "description": "Explicit routine id — overrides name resolution.",
            },
            "instructions": {
                "type": "string",
                "description": "For 'create': what the routine should do (required).",
            },
            "cron": {
                "type": "string",
                "description": (
                    "For 'create': an optional cron expression for a scheduled "
                    "trigger. (Auto-run isn't live yet — use 'run' to execute.)"
                ),
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="web_routine",
    schema=_SCHEMA,
    handler=_handle_web_routine,
    toolset="web",
    check_fn=None,
    is_async=True,
)
