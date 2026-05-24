"""propose_code_mod tool — voice trigger for the auto-mod loop (Spec B, Plane 3).

Registered only when JARVIS_AUTOMOD_ENABLED=1. The supervisor calls this
tool to enqueue an explicit code-mod intent (e.g. user said
'Jarvis, fix the bug where you keep saying sir'). The spawner picks it
up out of band.

This is the explicit-request path; the pattern detector handles the
implicit-recurrence path. Both write to the same queue.jsonl.

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

from pipeline.automod._state import queue_path
from tools.registry import registry, tool_error

logger = logging.getLogger("jarvis.code_mod")


def is_available() -> bool:
    """check_fn: tool present only when JARVIS_AUTOMOD_ENABLED=1."""
    return os.environ.get("JARVIS_AUTOMOD_ENABLED", "0") == "1"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _next_id() -> str:
    suffix = hashlib.sha1(
        f"explicit-{time.time_ns()}".encode()
    ).hexdigest()[:6]
    return f"automod-{time.strftime('%Y-%m-%d', time.gmtime())}-{suffix}"


def _handle_propose(args: dict) -> str:
    intent = str(args.get("intent", "")).strip()
    rationale = str(args.get("rationale", "")).strip()
    if not intent:
        return tool_error("intent is required (non-empty)", success=False)
    if not rationale:
        return tool_error("rationale is required (non-empty)", success=False)

    rec_id = _next_id()
    record = {
        "id": rec_id,
        "kind": "explicit",
        "intent": intent,
        "rationale": rationale,
        "created_at": _now_iso(),
    }
    p = queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("[automod] explicit propose: id=%s intent=%r",
                rec_id, intent[:80])
    return json.dumps({
        "success": True,
        "id": rec_id,
        "message": f"Code-mod intent queued as {rec_id}. Manual review "
                   f"will follow via bin/jarvis-automod.",
    })


CODE_MOD_SCHEMA = {
    "name": "propose_code_mod",
    "description": (
        "Propose a source-code modification when no skill / memory / "
        "procedure path can fix the issue. Use SPARINGLY — only when "
        "the user explicitly asks you to fix a recurring bug, add a "
        "tool, or patch a prompt. The proposal opens a branch + runs "
        "tests + writes an artifact for the user to manually merge. "
        "Do NOT use for routine memory / preference saves (use memory() "
        "instead) or skill authoring (the autonomous reviewer handles "
        "those). Required: intent (one-sentence description of the "
        "change), rationale (why this needs code, not memory / skill)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "One-sentence description of the change.",
            },
            "rationale": {
                "type": "string",
                "description": "Why this needs a code change instead of "
                               "memory/skill/procedure.",
            },
        },
        "required": ["intent", "rationale"],
    },
}


registry.register(
    name="propose_code_mod",
    toolset="automod",
    schema=CODE_MOD_SCHEMA,
    handler=lambda args, **_kw: _handle_propose(args),
    check_fn=is_available,
    requires_env=["JARVIS_AUTOMOD_ENABLED"],
    is_async=False,
    emoji="🔧",
)
