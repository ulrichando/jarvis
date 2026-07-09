"""web_code — voice control over the JARVIS web app's coding sessions.

Lets JARVIS start / list / check the web app's ``/code`` sessions by voice —
e.g. "start a code task on my repo to add tests to the parser", "what are my
code sessions doing", "status of the parser task".

``start`` dispatches a real coding task: it resolves a target environment
(machine/container), seeds the prompt, and a container/session spins up — this
spends compute, so it defaults to the ``acceptEdits`` permission mode (not
bypass) and reports the session id for you to watch in the Code tab. It does NOT
stream the run back; use ``status`` (or the web UI) to check progress.

Auth: the shared ``JARVIS_LOCAL_API_TOKEN`` bearer via :mod:`tools._web_api`;
the tasks/sessions/environments routes resolve it to the box owner.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import _web_api
from .registry import registry, tool_error

_SESSIONS = "/api/bridge/v1/sessions"
_TASKS = "/api/bridge/v1/tasks"
_ENVS = "/api/bridge/v1/environments"


def _fmt_sessions(sessions: List[Dict[str, Any]]) -> str:
    if not sessions:
        return "You have no code sessions."
    parts = []
    for s in sessions[:10]:
        title = str(s.get("title") or "Session").strip()[:50]
        status = str(s.get("status") or "").strip()
        parts.append(f"{title} — {status}" if status else title)
    n = len(sessions)
    more = f" (+{n - len(parts)} more)" if n > len(parts) else ""
    head = "1 code session" if n == 1 else f"{n} code sessions"
    return f"You have {head}: " + "; ".join(parts) + more + "."


async def _resolve_environment(args: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (environment_id, error). Pick the named env, else first online."""
    data = await _web_api.call_web("GET", _ENVS)
    envs = data.get("environments") if isinstance(data, dict) else None
    if not isinstance(envs, list) or not envs:
        return None, "no environments are registered — start a bridge worker or a cloud env first."
    named = (args.get("environment") or "").strip() if isinstance(args.get("environment"), str) else ""
    if named:
        m = [e for e in envs if str(e.get("machine_name") or "").strip().lower() == named.lower()]
        if not m:
            avail = ", ".join(str(e.get("machine_name") or "?") for e in envs[:6])
            return None, f"no environment named {named!r}. Available: {avail}."
        return str(m[0].get("environment_id") or ""), None
    # default: first online, else first
    online = [e for e in envs if e.get("online")]
    pick = (online or envs)[0]
    return str(pick.get("environment_id") or ""), None


async def _handle_web_code(args: Dict[str, Any]) -> str:
    args = args or {}
    action = (args.get("action") or "").strip().lower()

    try:
        if action == "list":
            data = await _web_api.call_web("GET", _SESSIONS)
            rows = data.get("sessions", []) if isinstance(data, dict) else []
            return _fmt_sessions(rows)

        if action == "status":
            name = (args.get("name") or "").strip() if isinstance(args.get("name"), str) else ""
            data = await _web_api.call_web("GET", _SESSIONS)
            rows = data.get("sessions", []) if isinstance(data, dict) else []
            if not name:
                return _fmt_sessions(rows)
            match = [
                s for s in rows
                if name.lower() in str(s.get("title") or "").lower()
            ]
            if not match:
                return tool_error(f"web_code: no code session matching {name!r}.")
            s = match[0]
            return f"{str(s.get('title') or 'Session')}: {str(s.get('status') or 'unknown')}."

        if action == "start":
            prompt = (args.get("prompt") or "").strip()
            if not prompt:
                return tool_error(
                    "web_code: start needs a prompt — what should the coding task do?"
                )
            env_id, err = await _resolve_environment(args)
            if err:
                return tool_error(f"web_code: {err}")
            # Voice-dispatched tasks spend compute and act on a repo — default to
            # acceptEdits (a human still approves risky ops), never bypass.
            body: Dict[str, Any] = {
                "environment_id": env_id,
                "prompt": prompt,
                "permission_mode": "acceptEdits",
            }
            if isinstance(args.get("model"), str) and args["model"].strip():
                body["model"] = args["model"].strip()
            data = await _web_api.call_web("POST", _TASKS, json_body=body)
            sid = (data or {}).get("session_id") if isinstance(data, dict) else None
            tail = f" (session {sid})" if sid else ""
            return (
                f"Started a code task{tail}. Watch it in the Code tab; "
                "ask me for its status anytime."
            )

        return tool_error(
            f"web_code: unknown action {action!r} (expected list, start, or status)."
        )
    except _web_api.WebError as e:
        return tool_error(f"web_code: {e}")


_SCHEMA = {
    "name": "web_code",
    "description": (
        "Start, list, or check the JARVIS web app's coding sessions (the /code "
        "tab). 'start' dispatches a real coding task on a repo — it spins up a "
        "container/session (spends compute), defaults to a safe permission mode, "
        "and reports the session id to watch in the web UI (it does NOT stream "
        "the run back — use 'status' to check). Use when the user asks to kick "
        "off or check coding work by voice — e.g. 'start a code task to add "
        "tests to the parser', 'what are my code sessions doing', 'status of the "
        "parser task'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "start", "status"],
                "description": "What to do.",
            },
            "prompt": {
                "type": "string",
                "description": "For 'start': what the coding task should do (required).",
            },
            "environment": {
                "type": "string",
                "description": (
                    "For 'start': which environment/machine to run on (by name). "
                    "Omit to use the first online environment."
                ),
            },
            "model": {
                "type": "string",
                "description": "For 'start': optional model override for the task.",
            },
            "name": {
                "type": "string",
                "description": "For 'status': match a session by (part of) its title.",
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="web_code",
    schema=_SCHEMA,
    handler=_handle_web_code,
    toolset="web",
    check_fn=None,
    is_async=True,
)
