"""Agents surface — list / create / edit / delete user subagents.

Two tools exposed through the registry framework:

  agents_list   — enumerate dispatchable agents (built-in + user-authored)
  agent_manage  — create / edit / patch / delete user agents

Wires to pipeline/agent_authoring.py (create/edit/delete/discover) and reads
the built-in dispatch roster from tools/dispatch_agent.py::_POLICY. The
files it writes (``~/.jarvis/agents/<name>.md``) are exactly what
``dispatch_agent(subagent_type="<name>")`` spawns via ``bin/jarvis --agent``,
so a created agent is dispatchable immediately — the same parity skills have
between skill_manage and skill_view.

Import order mirrors skills_tool.py: registry.py has no pipeline deps; the
pipeline + dispatch imports are deferred to handler time to avoid any
circular-import issues during tool discovery.
"""
from __future__ import annotations

import logging

from .registry import registry

logger = logging.getLogger("jarvis.agent_authoring_tool")

# ---------------------------------------------------------------------------
# agents_list
# ---------------------------------------------------------------------------


def _builtin_dispatch_rows() -> list[tuple[str, str]]:
    """(name, blurb) for the built-in dispatch agents, read from the live
    _POLICY so this never drifts from what dispatch_agent actually accepts."""
    blurbs = {
        "explore": "fast file/code search across the repo",
        "researcher": "deep web research with sources",
        "code_reviewer": "review the uncommitted diff against project rules",
        "plan": "design how to implement a feature",
    }
    try:
        from tools.dispatch_agent import _POLICY  # lazy — avoid load-order issues
    except Exception:  # pragma: no cover — defensive
        return []
    return [(name, blurbs.get(name, "built-in dispatch agent")) for name in _POLICY]


def _handle_agents_list(args: dict) -> str:  # noqa: ARG001 — no params
    """Compact, voice-friendly list of every dispatchable agent."""
    from pipeline import agent_authoring as aa  # lazy import

    builtins = _builtin_dispatch_rows()
    customs = aa.discover_agents()

    lines: list[str] = []
    if builtins:
        lines.append("Built-in:")
        lines.extend(f"  • {name} — {blurb}" for name, blurb in builtins)
    if customs:
        lines.append("Custom (user-authored):")
        for a in customs:
            tag = "" if a["editable"] else " (project, read-only)"
            desc = a["description"] or "(no description)"
            if len(desc) > 140:
                desc = desc[:137].rstrip() + "…"
            lines.append(f"  • {a['name']}{tag} — {desc}")

    if not lines:
        return "No agents available."
    total = len(builtins) + len(customs)
    return f"{total} agent(s) dispatchable:\n" + "\n".join(lines)


_AGENTS_LIST_SCHEMA = {
    "name": "agents_list",
    "description": (
        "List every agent you can dispatch with dispatch_agent — the four "
        "built-ins (explore / researcher / code_reviewer / plan) plus any "
        "user-authored agents created with agent_manage. Returns each agent's "
        "name and when to use it. Call before dispatching a custom agent."
    ),
    "parameters": {"type": "object", "properties": {}},
}

registry.register(
    name="agents_list",
    schema=_AGENTS_LIST_SCHEMA,
    handler=_handle_agents_list,
    toolset="agents",
    is_async=False,
    description=_AGENTS_LIST_SCHEMA["description"],
    emoji="🧭",
)

# ---------------------------------------------------------------------------
# agent_manage
# ---------------------------------------------------------------------------

_MANAGE_ACTIONS = ("create", "edit", "patch", "delete")


def _handle_agent_manage(args: dict) -> str:  # noqa: C901 — dispatcher
    """Dispatch agent authoring actions to the authoring core."""
    from pipeline import agent_authoring as aa  # lazy import

    action = (args.get("action") or "").strip().lower()
    if action not in _MANAGE_ACTIONS:
        return (
            f"(agent_manage: unknown action {action!r}. "
            f"Valid actions: {', '.join(_MANAGE_ACTIONS)})"
        )

    name = (args.get("name") or "").strip()
    if not name:
        return "(agent_manage: 'name' is required)"

    if action == "create":
        description = (args.get("description") or "").strip()
        body = (args.get("body") or "").strip()
        if not description:
            return "(agent_manage create: 'description' is required)"
        if not body:
            return "(agent_manage create: 'body' is required)"
        res = aa.create_user_agent(
            name, description, body, args.get("tools"), args.get("model")
        )

    elif action == "edit":
        body = (args.get("body") or "").strip()
        if not body:
            return "(agent_manage edit: 'body' is required)"
        res = aa.edit_user_agent(
            name,
            body,
            description=args.get("description"),  # None → preserve frontmatter
            tools=args.get("tools"),
            model=args.get("model"),
        )

    elif action == "patch":
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if old_string is None or new_string is None:
            return "(agent_manage patch: 'old_string' and 'new_string' are required)"
        res = aa.patch_user_agent(name, old_string, new_string, bool(args.get("replace_all", False)))

    elif action == "delete":
        res = aa.delete_user_agent(name)

    else:  # pragma: no cover — guarded above
        return f"(agent_manage: unhandled action {action!r})"

    if res.get("ok"):
        if action == "create":
            shadow_note = " (shadows a built-in/project agent name)" if res.get("shadow") else ""
            return (
                f"Agent {name!r} created and ready to dispatch"
                f" via dispatch_agent(subagent_type={name!r}).{shadow_note}"
            )
        if action == "edit":
            return f"Agent {name!r} updated."
        if action == "patch":
            return f"Agent {name!r} patched."
        if action == "delete":
            return f"Agent {name!r} deleted (recoverable from trash)."
    return f"Error: {res.get('error', 'unknown error')}"


_AGENT_MANAGE_SCHEMA = {
    "name": "agent_manage",
    "description": (
        "Create, edit, or delete a user subagent — a named specialist with its "
        "own system prompt and tool set, dispatchable via dispatch_agent. "
        "Actions: 'create' (name + description + body required; tools/model optional), "
        "'edit' (name + body required; pass description to also rewrite the when-to-use), "
        "'patch' (name + old_string + new_string required; replace_all optional), "
        "'delete' (name required — moved to trash, recoverable). "
        "Built-in agents (explore/researcher/code_reviewer/plan) and project "
        "agents are read-only; only user agents (~/.jarvis/agents/) can be modified. "
        "A created agent is dispatchable immediately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_MANAGE_ACTIONS),
                "description": "The authoring action to perform.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Agent name (letters, digits, hyphens; 3-50 chars; starts/ends "
                    "alphanumeric — e.g. 'release-notes-writer'). This is the value "
                    "you later pass as dispatch_agent's subagent_type."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "When to use this agent (required for 'create'). Becomes the "
                    "agent's discovery description."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "The agent's full system prompt — its role, method, and output "
                    "expectations. Required for 'create' and 'edit'; min 20 chars."
                ),
            },
            "tools": {
                "type": "string",
                "description": (
                    "Optional comma-separated tool allow-list (e.g. 'Read, Grep, "
                    "WebSearch'). Omit to let the agent inherit all tools."
                ),
            },
            "model": {
                "type": "string",
                "description": "Optional model override (e.g. 'inherit', 'sonnet', 'opus').",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to replace (required for 'patch').",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text (required for 'patch').",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string. Default false.",
            },
        },
        "required": ["action", "name"],
    },
}

registry.register(
    name="agent_manage",
    schema=_AGENT_MANAGE_SCHEMA,
    handler=_handle_agent_manage,
    toolset="agents",
    is_async=False,
    description=_AGENT_MANAGE_SCHEMA["description"],
    emoji="🤖",
)
