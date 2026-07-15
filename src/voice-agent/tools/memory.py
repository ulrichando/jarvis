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

Design spec: docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md.
"""
from __future__ import annotations

import json
import logging
import re

from pipeline import file_memory
from tools.registry import registry, tool_error

_PROCEDURE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

logger = logging.getLogger("jarvis.memory")


def is_available() -> bool:
    """File-backed memory has no external dependency — always available.
    Kept as a function because jarvis_agent imports it as a feature gate."""
    return True


# ── Tool handler ──────────────────────────────────────────────────────


def _mirror_to_local(action: str, target: str, args: dict) -> None:
    """Best-effort replay of a SUCCESSFUL cloud write into the local
    file-backed store, so the offline-fallback files stay warm mid-session
    (JARVIS_CLOUD_MEMORY=1 only). Replays through the normal file_memory
    functions — including the procedure ``## name`` heading prepend the
    local path applies — and swallows every error (the cloud store already
    holds the truth; a mirror miss only staples the mirror one session
    behind)."""
    try:
        content = args.get("content")
        old_text = args.get("old_text")
        if action == "add":
            if target == "procedure":
                name = str(args.get("name", "")).strip()
                if name:
                    content = f"## {name}\n{content}"
            file_memory.add(target, content or "")
        elif action == "replace":
            file_memory.replace(target, old_text or "", content or "")
        elif action == "remove":
            file_memory.remove(target, old_text or "")
    except Exception as exc:  # noqa: BLE001 — mirror is best-effort
        logger.debug("[memory] local mirror failed (%s %s): %s", action, target, exc)


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
        return tool_error(f"Invalid target {target!r}. Use 'memory', 'user', or 'procedure'.", success=False)

    # Cloud-primary path (JARVIS_CLOUD_MEMORY=1, pipeline.cloud_memory):
    # POST the RAW args to the shared cloud store — the server runs the
    # identical contract (budgets, scan, kebab-name + procedure-heading
    # handling) and returns the tool JSON. Contract failures come back
    # HTTP 200 success:false and are returned VERBATIM (tool results the
    # model self-corrects on — no local fallback for those); only
    # transport/HTTP errors (raise) fall through to the unchanged local
    # path below, so offline behavior is exactly today's. This handler is
    # sync and runs in asyncio.to_thread (tools/_adapter.py), so the
    # bounded blocking POST never stalls the voice loop.
    try:
        from pipeline import cloud_memory
        _cloud_on = cloud_memory.enabled()
    except Exception:  # noqa: BLE001 — cloud layer must never break the tool
        _cloud_on = False
    # Set on a cloud TRANSPORT failure (post_memory_tool raised) for a write
    # action: after the local fallback below succeeds, the RAW args are
    # journaled (cloud_memory.journal_pending_write) so the next online
    # session replays them BEFORE mirroring — otherwise the snapshot mirror
    # (which never saw this write) would clobber it (data loss). HTTP-200
    # success:false contract failures return above and are NEVER journaled —
    # those are authoritative rejections, not lost writes.
    _journal_args = None
    if _cloud_on:
        try:
            result = cloud_memory.post_memory_tool(
                {
                    "user_id": cloud_memory.user_id(),
                    "action": action,
                    "target": target,
                    "content": content,
                    "old_text": old_text,
                    "name": args.get("name"),
                }
            )
            if action != "read" and result.get("success"):
                _mirror_to_local(action, target, args)
            msg = result.get("message") or result.get("error")
            if msg:
                logger.info("[memory] cloud %s %s → %s", action, target, msg)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 — transport error → local fallback
            logger.warning("[memory] cloud write failed (%s); falling back to local", e)
            if action in ("add", "replace", "remove"):
                # Capture the RAW tool args NOW — the local 'add' path below
                # mutates `content` for procedures (## heading prepend); the
                # cloud contract wants the raw name + content.
                _journal_args = {
                    "action": action,
                    "target": target,
                    "content": content,
                    "old_text": old_text,
                    "name": args.get("name"),
                }

    if action == "add":
        if not content:
            return tool_error("content is required for 'add'.", success=False)
        if target == "procedure":
            name = str(args.get("name", "")).strip()
            if not name:
                return tool_error("name is required for action='add' with target='procedure'. Use kebab-case (e.g. 'deploy-app').", success=False)
            if not _PROCEDURE_NAME_RE.match(name):
                return tool_error(f"name {name!r} is not kebab-case. Use lowercase letters/digits/dashes only.", success=False)
            # Prepend the name as a heading so the entry is self-describing
            # in the snapshot. The supervisor's prompt sees "## deploy-app\n1. ...".
            content = f"## {name}\n{content}"
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

    # Journal-and-replay (see comment at _journal_args above): journal only
    # writes the LOCAL store actually accepted — a local contract failure
    # (budget / scan / no-match) means nothing landed anywhere, so there is
    # nothing to lose and nothing to replay.
    if _journal_args is not None and isinstance(result, dict) and result.get("success"):
        try:
            from pipeline import cloud_memory
            cloud_memory.journal_pending_write(_journal_args)
        except Exception as exc:  # noqa: BLE001 — journaling must never break the tool
            logger.warning("[memory] could not journal offline cloud write: %s", exc)

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
        "useful again\n"
        "- He asks you to 'save this process' or 'remember how to X' — "
        "store as target='procedure' with a kebab-case name and numbered steps\n\n"
        "THREE STORES (the 'target'):\n"
        "- 'user' (USER.md): who Ulrich is — role, background, preferences, "
        "communication style, pet peeves.\n"
        "- 'memory' (MEMORY.md): your own notes — environment facts, project "
        "conventions, tool quirks, lessons learned.\n"
        "- 'procedure' (PROCEDURES.md): named multi-step processes Ulrich "
        "wants to invoke later. Requires 'name' (kebab-case, e.g. "
        "'deploy-app') and 'content' as a numbered step list.\n\n"
        "ACTIONS:\n"
        "- add     — store a new entry (needs 'content'; procedure also needs 'name').\n"
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
                "enum": ["memory", "user", "procedure"],
                "description": "Which store: 'user' for Ulrich's profile, 'memory' for your own notes, 'procedure' for named multi-step processes.",
            },
            "content": {
                "type": "string",
                "description": "The entry text. Required for 'add' and 'replace'. For target='procedure', supply a numbered step list (e.g. '1. step one\\n2. step two').",
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove.",
            },
            "name": {
                "type": "string",
                "description": "Kebab-case identifier (e.g. 'deploy-app'). Required when target='procedure' and action='add'.",
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
