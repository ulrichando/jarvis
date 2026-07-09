"""web_workspace — voice control over the JARVIS web app's workspaces.

Lets JARVIS list / create / configure / delete the web app's **workbench** and
**design** workspaces by voice — the same objects the web UI's Workbench and
Design tabs manage (indexed in ``~/.jarvis/workspaces/_meta.json``, served by
``/api/workspace``). Backend actions, not UI puppeteering: state lands in the
web app and shows up next time the user opens it.

Voice-natural addressing: ``configure`` / ``delete`` accept a workspace ``name``
(resolved to its id via the list) as well as a raw ``id``. An ambiguous name
(two workspaces share it) returns an error asking the user to disambiguate
rather than guessing — deletion in particular must never hit the wrong one.

Auth: the shared ``JARVIS_LOCAL_API_TOKEN`` bearer via :mod:`tools._web_api`.
The ``/api/workspace`` handlers do NOT re-check a user cookie, so the bearer is
sufficient end to end with no web-side change.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import _web_api
from .registry import registry, tool_error

_KINDS = ("workbench", "design")


def _fmt_list(workspaces: List[Dict[str, Any]], kind: Optional[str]) -> str:
    label = f"{kind} " if kind else ""
    if not workspaces:
        return f"No {label}workspaces yet."
    names = [str(w.get("name") or "untitled") for w in workspaces]
    n = len(names)
    if n == 1:
        return f"You have 1 {label}workspace: {names[0]}."
    listed = ", ".join(names[:-1]) + f", and {names[-1]}"
    return f"You have {n} {label}workspaces: {listed}."


async def _resolve_id(args: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (workspace_id, error). Prefer an explicit id; else resolve name."""
    wid = (args.get("id") or "").strip() if isinstance(args.get("id"), str) else ""
    if wid:
        return wid, None
    name = (args.get("name") or "").strip() if isinstance(args.get("name"), str) else ""
    if not name:
        return None, "which workspace? Give a name or id."
    data = await _web_api.call_web("GET", "/api/workspace")
    all_ws = data.get("workspaces") if isinstance(data, dict) else None
    if not isinstance(all_ws, list):
        return None, "couldn't read the workspace list to resolve that name."
    matches = [w for w in all_ws if str(w.get("name") or "").strip().lower() == name.lower()]
    if not matches:
        return None, f"no workspace named {name!r}."
    if len(matches) > 1:
        return None, f"more than one workspace is named {name!r} — say the id instead."
    return str(matches[0].get("id") or ""), None


async def _handle_web_workspace(args: Dict[str, Any]) -> str:
    args = args or {}
    action = (args.get("action") or "").strip().lower()

    try:
        if action == "list":
            kind = args.get("kind")
            kind = kind if kind in _KINDS else None
            params = {"kind": kind} if kind else None
            data = await _web_api.call_web("GET", "/api/workspace", params=params)
            ws = data.get("workspaces", []) if isinstance(data, dict) else []
            return _fmt_list(ws, kind)

        if action == "create":
            name = (args.get("name") or "").strip()
            if not name:
                return tool_error("web_workspace: create needs a name.")
            kind = args.get("kind")
            kind = kind if kind in _KINDS else "workbench"
            data = await _web_api.call_web(
                "POST", "/api/workspace", json_body={"name": name, "kind": kind}
            )
            ws = data.get("workspace") if isinstance(data, dict) else None
            made = str((ws or {}).get("name") or name)
            return f"Created {kind} workspace {made!r}."

        if action == "configure":
            wid, err = await _resolve_id(args)
            if err:
                return tool_error(f"web_workspace: {err}")
            patch: Dict[str, Any] = {}
            if isinstance(args.get("rename"), str) and args["rename"].strip():
                patch["name"] = args["rename"].strip()
            if isinstance(args.get("instructions"), str):
                patch["customInstructions"] = args["instructions"]
            if isinstance(args.get("deploy"), str) and args["deploy"].strip():
                patch["deploy"] = args["deploy"].strip()
            if not patch:
                return tool_error(
                    "web_workspace: configure needs something to change "
                    "(rename, instructions, or deploy)."
                )
            data = await _web_api.call_web(
                "PATCH", f"/api/workspace/{wid}", json_body=patch
            )
            ws = data.get("workspace") if isinstance(data, dict) else None
            nm = str((ws or {}).get("name") or args.get("rename") or args.get("name") or wid)
            return f"Updated workspace {nm!r}."

        if action == "delete":
            wid, err = await _resolve_id(args)
            if err:
                return tool_error(f"web_workspace: {err}")
            await _web_api.call_web("DELETE", f"/api/workspace/{wid}")
            nm = str(args.get("name") or wid)
            return f"Deleted workspace {nm!r}."

        return tool_error(
            f"web_workspace: unknown action {action!r} "
            "(expected list, create, configure, or delete)."
        )
    except _web_api.WebError as e:
        return tool_error(f"web_workspace: {e}")


_SCHEMA = {
    "name": "web_workspace",
    "description": (
        "List, create, configure, or delete the JARVIS web app's workspaces "
        "(the Workbench and Design tabs). Use this when the user asks to manage "
        "their web workspaces by voice — e.g. 'list my workbench workspaces', "
        "'make a new workbench called Notes', 'rename the Notes workspace to "
        "Ideas', 'delete the Scratch workspace'. Addresses workspaces by name "
        "(resolved automatically) or id. This acts on the web app's real state, "
        "not the local desktop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "configure", "delete"],
                "description": "What to do.",
            },
            "kind": {
                "type": "string",
                "enum": list(_KINDS),
                "description": (
                    "Workspace kind. For 'list', filters to that kind; for "
                    "'create', defaults to 'workbench'."
                ),
            },
            "name": {
                "type": "string",
                "description": (
                    "For 'create': the new workspace's name. For 'configure'/"
                    "'delete': the name of the workspace to target (resolved to "
                    "its id; ambiguous names are rejected)."
                ),
            },
            "id": {
                "type": "string",
                "description": "Explicit workspace id — overrides name resolution.",
            },
            "rename": {
                "type": "string",
                "description": "For 'configure': the new name to give the workspace.",
            },
            "instructions": {
                "type": "string",
                "description": (
                    "For 'configure': workspace-scoped custom instructions "
                    "(appended to the chat system prompt for that workspace)."
                ),
            },
            "deploy": {
                "type": "string",
                "description": "For 'configure': the workspace's deploy target.",
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="web_workspace",
    schema=_SCHEMA,
    handler=_handle_web_workspace,
    toolset="web",
    check_fn=None,
    is_async=True,
)
