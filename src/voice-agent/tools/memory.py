"""Memory tool — durable, file-backed user-facts that survive chat deletion.

Deliberate-writes model: the supervisor decides what is worth keeping via a
single ``memory`` tool. Two file-backed stores under
``get_jarvis_home()/"memories"`` (see ``pipeline.file_memory``):

  - ``user``   → USER.md   : who Ulrich is (role, preferences, style).
  - ``memory`` → MEMORY.md : JARVIS's own notes (environment, conventions,
    tool quirks, lessons learned).

Both are injected into the system prompt as a FROZEN snapshot at session
start (``pipeline.file_memory.snapshot_for_prompt``), so the model always
sees current memory without a recall round-trip. Mid-session writes persist
to disk but don't churn the prompt — preserving the prefix cache.

The tool is registered into the registry framework (``tools.registry``) and
adapted to a LiveKit ``RawFunctionTool`` by ``tools._adapter`` at session
start, so it appears on the supervisor's tool surface like any other tool.

History: this module previously held hub-backed ``remember`` / ``forget`` /
``list_memories`` / ``audit_memories`` ``@function_tool`` defs plus an
auto-extractor that wrote to the hub ``events:memory`` stream. That whole
path was swapped for the file-backed model on 2026-05-21 — see
``pipeline.file_memory`` and
docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md.
"""
from __future__ import annotations

import json
import logging

from pipeline import file_memory
from tools.registry import registry, tool_error

logger = logging.getLogger("jarvis.memory")


def is_available() -> bool:
    """File-backed memory has no external dependency — always available.
    Kept as a function because jarvis_agent imports it as a feature gate."""
    return True


# ── Tool handler ──────────────────────────────────────────────────────


def _handle_memory(args: dict) -> str:
    """Dispatch a ``memory`` tool call to the file-backed store.

    Returns a JSON string (the registry adapter str-coerces, but JSON keeps
    the response machine-parsable + gives the model the live entry list +
    usage so it can self-correct on a char-limit error)."""
    action = str(args.get("action", "")).strip().lower()
    target = str(args.get("target", "memory")).strip().lower()
    content = args.get("content")
    old_text = args.get("old_text")

    if target not in file_memory.VALID_TARGETS:
        return tool_error(f"Invalid target {target!r}. Use 'memory' or 'user'.", success=False)

    if action == "add":
        if not content:
            return tool_error("content is required for 'add'.", success=False)
        result = file_memory.add(target, content)
    elif action == "replace":
        if not old_text:
            return tool_error("old_text is required for 'replace'.", success=False)
        if not content:
            return tool_error("content is required for 'replace'.", success=False)
        result = file_memory.replace(target, old_text, content)
    elif action == "remove":
        if not old_text:
            return tool_error("old_text is required for 'remove'.", success=False)
        result = file_memory.remove(target, old_text)
    elif action == "read":
        result = file_memory.read(target)
    else:
        return tool_error(
            f"Unknown action {action!r}. Use: add, replace, remove, read.",
            success=False,
        )

    if isinstance(result, dict):
        msg = result.get("message") or result.get("error")
        if msg:
            logger.info("[memory] %s %s → %s", action, target, msg)
    return json.dumps(result, ensure_ascii=False)


# ── Schema + registration ─────────────────────────────────────────────

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save or update durable information that survives across sessions. "
        "Memory is injected into your system prompt at the start of every "
        "session, so keep entries compact and focused on facts that will "
        "still matter later.\n\n"
        "WHEN TO SAVE (proactively — don't wait to be asked):\n"
        "- Ulrich corrects you or says 'remember this' / 'don't do that again'\n"
        "- He shares a preference, habit, or personal detail (name, role, "
        "timezone, how he likes replies)\n"
        "- You learn a stable fact about his work or environment that will be "
        "useful again\n\n"
        "TWO STORES (the 'target'):\n"
        "- 'user' (USER.md): who Ulrich is — role, background, preferences, "
        "communication style, pet peeves.\n"
        "- 'memory' (MEMORY.md): your own notes — environment facts, project "
        "conventions, tool quirks, lessons learned.\n\n"
        "ACTIONS:\n"
        "- add     — store a new entry (needs 'content').\n"
        "- replace — update an existing entry; 'old_text' is a short unique "
        "substring identifying it, 'content' is the new text.\n"
        "- remove  — delete an entry; 'old_text' identifies it.\n"
        "- read    — list the live entries in a store (use to audit before "
        "editing).\n\n"
        "DO save before replying when Ulrich states something durable about "
        "his life or work — silent, no need to announce it.\n"
        "DON'T save: code patterns, file paths, git history, debug recipes, "
        "anything already in your instructions, ephemeral state ('I'm hungry', "
        "'working on X right now'), or credentials. Write plain assertions, "
        "never narration ('The user is asking about…')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "read"],
                "description": "What to do.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which store: 'user' for Ulrich's profile, 'memory' for your own notes.",
            },
            "content": {
                "type": "string",
                "description": "The entry text. Required for 'add' and 'replace'.",
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove.",
            },
        },
        "required": ["action", "target"],
    },
}


registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **_kw: _handle_memory(args),
    check_fn=is_available,
    is_async=False,
    emoji="🧠",
)
