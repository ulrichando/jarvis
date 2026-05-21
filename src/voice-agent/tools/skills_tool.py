"""Skills surface — list / view / manage voice-agent skills.

Three tools exposed through the registry framework:

  skills_list    — enumerate available skills (voice-friendly compact form)
  skill_view     — progressive disclosure: full markdown body of a named skill
  skill_manage   — create / patch / edit / delete user skills

Wires to the existing skills engine in pipeline/skills_loader.py
(SKILLS registry singleton) and pipeline/skills_authoring.py
(create_user_skill, patch_user_skill, edit_user_skill, delete_user_skill).

Import order: registry.py has no deps on pipeline/; skills_loader loads on
first import; we defer that import to handler time to avoid any circular-import
issues during tool discovery.
"""
from __future__ import annotations

import logging

from .registry import registry

logger = logging.getLogger("jarvis.skills_tool")

# ---------------------------------------------------------------------------
# skills_list
# ---------------------------------------------------------------------------


def _handle_skills_list(args: dict) -> str:  # noqa: ARG001 — no params
    """Return a compact, voice-friendly list of all available skills."""
    from pipeline.skills_loader import SKILLS  # lazy import — avoids load-order issues

    skills = sorted(SKILLS.all(), key=lambda s: s.name)
    if not skills:
        return "No skills loaded. Add skill files to ~/.jarvis/skills/ or src/voice-agent/skills/."
    lines = [f"{sk.name} — {sk.when_to_use or sk.description}" for sk in skills]
    header = f"{len(lines)} skill(s) available:"
    return header + "\n" + "\n".join(f"  • {ln}" for ln in lines)


_SKILLS_LIST_SCHEMA = {
    "name": "skills_list",
    "description": (
        "List all available voice-agent skills. "
        "Returns each skill's name and a brief description of when to use it. "
        "Use before skill_view to discover what skills exist."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

registry.register(
    name="skills_list",
    schema=_SKILLS_LIST_SCHEMA,
    handler=_handle_skills_list,
    toolset="skills",
    is_async=False,
    description=_SKILLS_LIST_SCHEMA["description"],
    emoji="📋",
)

# ---------------------------------------------------------------------------
# skill_view
# ---------------------------------------------------------------------------


def _handle_skill_view(args: dict) -> str:
    """Return the full markdown body of a named skill."""
    from pipeline.skills_loader import SKILLS  # lazy import

    name = (args.get("name") or "").strip()
    if not name:
        return "(skill_view: 'name' is required)"

    sk = SKILLS.get(name)
    if sk is None:
        available = ", ".join(SKILLS.names())
        hint = f" Available: {available}" if available else " No skills loaded."
        return f"(unknown skill: {name!r}.{hint})"

    # Track usage telemetry for the curator. Viewing a skill loads its recipe
    # into the prompt path — that's both a "view" and an active "use". Both
    # bumps are best-effort and no-op for shipped (non-curatable) skills; a
    # telemetry failure must never break the tool call.
    try:
        from pipeline import skill_usage
        skill_usage.bump_view(name)
        skill_usage.record_use(name)
    except Exception:  # pragma: no cover — defensive; telemetry is best-effort
        pass

    header = f"# {sk.name}\n{sk.description}"
    if sk.when_to_use and sk.when_to_use != sk.description:
        header += f"\n\nWhen to use: {sk.when_to_use}"
    return header + "\n\n" + sk.body


_SKILL_VIEW_SCHEMA = {
    "name": "skill_view",
    "description": (
        "Retrieve the full recipe (markdown body) of a named skill. "
        "The returned content serves as a runtime instruction set for executing the skill. "
        "Call skills_list first to discover available skill names."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact skill name as returned by skills_list.",
            },
        },
        "required": ["name"],
    },
}

registry.register(
    name="skill_view",
    schema=_SKILL_VIEW_SCHEMA,
    handler=_handle_skill_view,
    toolset="skills",
    is_async=False,
    description=_SKILL_VIEW_SCHEMA["description"],
    emoji="📖",
)

# ---------------------------------------------------------------------------
# skill_manage
# ---------------------------------------------------------------------------

_MANAGE_ACTIONS = ("create", "patch", "edit", "delete")


def _handle_skill_manage(args: dict) -> str:  # noqa: C901 — dispatcher, tolerable complexity
    """Dispatch skill authoring actions to the skills engine."""
    from pipeline import skills_authoring as sa  # lazy import

    action = (args.get("action") or "").strip().lower()
    if action not in _MANAGE_ACTIONS:
        return (
            f"(skill_manage: unknown action {action!r}. "
            f"Valid actions: {', '.join(_MANAGE_ACTIONS)})"
        )

    name = (args.get("name") or "").strip()
    if not name:
        return "(skill_manage: 'name' is required)"

    if action == "create":
        description = (args.get("description") or "").strip()
        when_to_use = (args.get("when_to_use") or "").strip()
        body = (args.get("body") or "").strip()
        if not description:
            return "(skill_manage create: 'description' is required)"
        if not body:
            return "(skill_manage create: 'body' is required)"
        res = sa.create_user_skill(name, description, when_to_use, body)

    elif action == "patch":
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if old_string is None or new_string is None:
            return "(skill_manage patch: 'old_string' and 'new_string' are required)"
        replace_all = bool(args.get("replace_all", False))
        res = sa.patch_user_skill(name, old_string, new_string, replace_all)

    elif action == "edit":
        body = (args.get("body") or "").strip()
        if not body:
            return "(skill_manage edit: 'body' is required)"
        description = args.get("description")  # None → preserve existing
        when_to_use = args.get("when_to_use")  # None → preserve existing
        res = sa.edit_user_skill(name, body, description, when_to_use)

    elif action == "delete":
        res = sa.delete_user_skill(name)

    else:  # pragma: no cover — guarded above
        return f"(skill_manage: unhandled action {action!r})"

    if res.get("ok"):
        if action == "create":
            shadow_note = " (shadows a shipped skill)" if res.get("shadow") else ""
            return f"Skill {name!r} created.{shadow_note}"
        if action == "patch":
            return f"Skill {name!r} patched."
        if action == "edit":
            return f"Skill {name!r} updated."
        if action == "delete":
            return f"Skill {name!r} deleted (recoverable from trash)."
    return f"Error: {res.get('error', 'unknown error')}"


_SKILL_MANAGE_SCHEMA = {
    "name": "skill_manage",
    "description": (
        "Create, edit, or delete user skills. "
        "Actions: 'create' (name + description + body required, when_to_use optional), "
        "'patch' (name + old_string + new_string required, replace_all optional), "
        "'edit' (name + body required, description/when_to_use optional — omit to preserve), "
        "'delete' (name required — moved to trash, recoverable). "
        "Only user skills (~/.jarvis/skills/) can be modified; shipped skills are read-only."
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
                "description": "Skill name (lowercase letters, digits, hyphens; e.g. 'spotify-control').",
            },
            "description": {
                "type": "string",
                "description": "Short description of what the skill does (required for 'create').",
            },
            "when_to_use": {
                "type": "string",
                "description": "Condition/trigger description for when the supervisor should invoke this skill.",
            },
            "body": {
                "type": "string",
                "description": "Full markdown body (the recipe). Required for 'create' and 'edit'.",
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
    name="skill_manage",
    schema=_SKILL_MANAGE_SCHEMA,
    handler=_handle_skill_manage,
    toolset="skills",
    is_async=False,
    description=_SKILL_MANAGE_SCHEMA["description"],
    emoji="🛠",
)
