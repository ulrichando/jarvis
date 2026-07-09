"""web_project — voice control over the JARVIS web app's projects.

Lets JARVIS list / create / configure / delete the web app's **projects** (the
Projects tab) by voice — e.g. "list my projects", "make a project called Taxes",
"add instructions to the Taxes project", "favorite the Taxes project", "delete
the Scratch project". Backend actions against ``/api/projects``; state lands in
the web app's database.

Voice-natural addressing: ``configure`` / ``delete`` accept a project ``name``
(resolved to its id via the list) as well as a raw ``id``; an ambiguous name is
refused rather than guessed.

Auth: the shared ``JARVIS_LOCAL_API_TOKEN`` bearer via :mod:`tools._web_api`.
Projects routes are user-scoped, so they opt into the web app's
``requireUserIdOrSharedLocal`` helper — the shared token resolves to the
single-user id server-side (no browser cookie needed).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import _web_api
from .registry import registry, tool_error


def _fmt_list(projects: List[Dict[str, Any]]) -> str:
    if not projects:
        return "You have no projects yet."
    names = [str(p.get("name") or "untitled") for p in projects]
    n = len(names)
    if n == 1:
        return f"You have 1 project: {names[0]}."
    shown = names[:20]
    listed = ", ".join(shown[:-1]) + f", and {shown[-1]}"
    more = f" (+{n - len(shown)} more)" if n > len(shown) else ""
    return f"You have {n} projects: {listed}{more}."


async def _resolve_id(args: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (project_id, error). Prefer an explicit id; else resolve name."""
    pid = (args.get("id") or "").strip() if isinstance(args.get("id"), str) else ""
    if pid:
        return pid, None
    name = (args.get("name") or "").strip() if isinstance(args.get("name"), str) else ""
    if not name:
        return None, "which project? Give a name or id."
    data = await _web_api.call_web("GET", "/api/projects")
    rows = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None, "couldn't read the project list to resolve that name."
    matches = [p for p in rows if str(p.get("name") or "").strip().lower() == name.lower()]
    if not matches:
        return None, f"no project named {name!r}."
    if len(matches) > 1:
        return None, f"more than one project is named {name!r} — say the id instead."
    return str(matches[0].get("id") or ""), None


async def _handle_web_project(args: Dict[str, Any]) -> str:
    args = args or {}
    action = (args.get("action") or "").strip().lower()

    try:
        if action == "list":
            data = await _web_api.call_web("GET", "/api/projects")
            rows = data.get("projects", []) if isinstance(data, dict) else []
            return _fmt_list(rows)

        if action == "create":
            name = (args.get("name") or "").strip()
            if not name:
                return tool_error("web_project: create needs a name.")
            body: Dict[str, Any] = {"name": name}
            if isinstance(args.get("description"), str) and args["description"].strip():
                body["description"] = args["description"].strip()
            if isinstance(args.get("instructions"), str) and args["instructions"].strip():
                body["instructions"] = args["instructions"].strip()
            data = await _web_api.call_web("POST", "/api/projects", json_body=body)
            proj = data.get("project") if isinstance(data, dict) else None
            made = str((proj or {}).get("name") or name)
            return f"Created project {made!r}."

        if action == "configure":
            pid, err = await _resolve_id(args)
            if err:
                return tool_error(f"web_project: {err}")
            patch: Dict[str, Any] = {}
            if isinstance(args.get("rename"), str) and args["rename"].strip():
                patch["name"] = args["rename"].strip()
            if isinstance(args.get("description"), str):
                patch["description"] = args["description"]
            if isinstance(args.get("instructions"), str):
                patch["instructions"] = args["instructions"]
            if isinstance(args.get("favorite"), bool):
                patch["isFavorite"] = args["favorite"]
            if not patch:
                return tool_error(
                    "web_project: configure needs something to change "
                    "(rename, description, instructions, or favorite)."
                )
            data = await _web_api.call_web(
                "PATCH", f"/api/projects/{pid}", json_body=patch
            )
            proj = data.get("project") if isinstance(data, dict) else None
            nm = str((proj or {}).get("name") or args.get("rename") or args.get("name") or pid)
            if "isFavorite" in patch:
                return f"{'Favorited' if patch['isFavorite'] else 'Unfavorited'} project {nm!r}."
            return f"Updated project {nm!r}."

        if action == "delete":
            pid, err = await _resolve_id(args)
            if err:
                return tool_error(f"web_project: {err}")
            await _web_api.call_web("DELETE", f"/api/projects/{pid}")
            nm = str(args.get("name") or pid)
            return f"Deleted project {nm!r}."

        return tool_error(
            f"web_project: unknown action {action!r} "
            "(expected list, create, configure, or delete)."
        )
    except _web_api.WebError as e:
        return tool_error(f"web_project: {e}")


_SCHEMA = {
    "name": "web_project",
    "description": (
        "List, create, configure, or delete the JARVIS web app's projects (the "
        "Projects tab). Use when the user asks to manage their web projects by "
        "voice — e.g. 'list my projects', 'make a project called Taxes', 'add "
        "instructions to the Taxes project', 'favorite the Taxes project', "
        "'delete the Scratch project'. Addresses projects by name (resolved "
        "automatically) or id. Acts on the web app's real database."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "configure", "delete"],
                "description": "What to do.",
            },
            "name": {
                "type": "string",
                "description": (
                    "For 'create': the new project's name. For 'configure'/"
                    "'delete': the name of the project to target (resolved to "
                    "its id; ambiguous names are rejected)."
                ),
            },
            "id": {
                "type": "string",
                "description": "Explicit project id — overrides name resolution.",
            },
            "rename": {
                "type": "string",
                "description": "For 'configure': the new name to give the project.",
            },
            "description": {
                "type": "string",
                "description": "Project description (on create or configure).",
            },
            "instructions": {
                "type": "string",
                "description": (
                    "Project-scoped custom instructions (on create or configure)."
                ),
            },
            "favorite": {
                "type": "boolean",
                "description": "For 'configure': mark (true) or unmark (false) as favorite.",
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="web_project",
    schema=_SCHEMA,
    handler=_handle_web_project,
    toolset="web",
    check_fn=None,
    is_async=True,
)
