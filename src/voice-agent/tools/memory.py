"""Memory layer — durable user-facts that survive chat deletion.

Pattern: ChatGPT/Claude/Gemini "saved memories" — the LLM decides what
is worth keeping via tool calls. Stored in state.db.memories,
propagated through the hub bus (events:memory → broadcasts:memory).

Spec: docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.memory")

# Make src/hub importable without polluting sys.path globally — same
# pattern jarvis_agent.py uses.
_HUB_DIR = str(Path(__file__).parent.parent / "hub")
if _HUB_DIR not in sys.path:
    sys.path.insert(0, _HUB_DIR)


# ── Sensitive content blocklist — NEVER persist these. ───────────────
# Same regex shape as the unified-settings watcher uses for keys.env.
_SENSITIVE_RE = re.compile(
    r"("
    r"api[\s_-]?key"
    r"|secret"
    r"|password"
    r"|bearer\s+\w+"
    r"|sk-[a-zA-Z0-9]+"
    r"|ghp_\w+"
    r"|aws_(?:access|secret)_key"
    r"|token\s*[:=]"  # "token: xyz" / "token=xyz"
    r")",
    re.I,
)

_MAX_CONTENT_CHARS = 500
_VALID_CATEGORIES = ("identity", "preference", "project", "fact")


# ── Helpers (pure functions, easy to unit-test) ──────────────────────


def _normalize(text: str) -> str:
    return text.strip().lower()


def _memory_id(content: str) -> str:
    """Stable sha256 hex of normalized content. Same fact written twice
    → same id, so the apply path's ON CONFLICT keeps it as one row."""
    return hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()


def _is_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text))


# ── Hub I/O — kept thin so tests can monkeypatch ────────────────────


async def _publish_event_async(event_type: str, payload: dict) -> None:
    """Publish to events:memory via the hub Python SDK. Lazy-imported
    so this module loads even when the hub is unreachable."""
    from client import HubClient, MEMORY_EVENTS_STREAM
    # Ephemeral client per call — avoids holding a Redis connection
    # across the lifetime of the voice agent and keeps tests simple.
    hub = HubClient.from_url(source="voice")
    sid = os.environ.get("JARVIS_VOICE_SESSION_ID", "voice-default")
    try:
        await hub.publish(
            type=event_type,
            session_id=sid,
            payload=payload,
            stream=MEMORY_EVENTS_STREAM,
        )
    finally:
        try:
            await hub._redis.aclose()
        except Exception:
            pass


def _publish_event(event_type: str, payload: dict) -> None:
    """Sync wrapper used by tools (which are async via function_tool
    but the hub SDK is async). The function_tool harness already has
    an event loop; create a task instead of asyncio.run() so we don't
    spin up a second loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_publish_event_async(event_type, payload))
        else:
            loop.run_until_complete(_publish_event_async(event_type, payload))
    except RuntimeError:
        # No event loop — happens in tests. asyncio.run() is fine here.
        asyncio.run(_publish_event_async(event_type, payload))


def _read_memories_via_sdk(
    category: str | None = None, limit: int = 30,
) -> list[dict]:
    """Synchronous read against state.db. No Redis round-trip."""
    from client import HubClient
    return HubClient.read_memories_sync(category=category, limit=limit)


def _bump_uses_via_sdk(memory_ids: list[str]) -> None:
    from client import HubClient
    HubClient.bump_memory_use_sync(memory_ids)


# ── @function_tool entry points the LLM can call ────────────────────


@function_tool
async def remember(content: str, category: str = "fact") -> str:
    """Store a durable fact about the user. Use when the user shares
    something worth remembering across sessions: identity ('I live in
    Cameroon'), preferences ('I prefer terse replies'), projects ('I
    run Pretva, a ride-hailing service'), or lasting facts.

    Do NOT use for transient state ('right now I'm hungry') or for
    credentials/secrets — those are blocked.

    Args:
        content: The fact in one short sentence (≤500 chars).
        category: One of 'identity', 'preference', 'project', 'fact'.
    """
    text = (content or "").strip()
    if not text:
        return "(empty memory — nothing to save, sir)"
    if _is_sensitive(text):
        logger.info("[memory] blocked sensitive content")
        return "That looks like a credential, sir — I won't store it."
    if len(text) > _MAX_CONTENT_CHARS:
        return (
            f"Memory too long, sir — keep it under "
            f"{_MAX_CONTENT_CHARS} characters."
        )
    if category not in _VALID_CATEGORIES:
        category = "fact"

    mid = _memory_id(text)
    _publish_event("memory.value.upserted", {
        "memory_id": mid,
        "content": text,
        "category": category,
        "source_session_id": os.environ.get("JARVIS_VOICE_SESSION_ID"),
    })
    return "Saved, sir."


@function_tool
async def forget(query: str) -> str:
    """Remove a memory matching a query. Use when the user says
    'forget that I…' / 'remove the memory about X'.

    Args:
        query: Keyword(s) describing the memory to remove.
    """
    if not query or not query.strip():
        return "(no query — what should I forget, sir?)"

    candidates = _read_memories_via_sdk(limit=50)
    q = query.strip().lower()
    match = next(
        (m for m in candidates if q in m["content"].lower()),
        None,
    )
    if not match:
        return f"No match for {query!r}, sir."

    _publish_event("memory.value.removed", {"memory_id": match["memory_id"]})
    snippet = match["content"]
    if len(snippet) > 80:
        snippet = snippet[:77] + "…"
    return f"Forgotten: {snippet}"


@function_tool
async def list_memories(category: str | None = None) -> str:
    """List your saved memories. Use when the user asks 'what do you
    remember about me' or wants to audit what you know.

    Args:
        category: Optional filter — 'identity', 'preference',
                  'project', or 'fact'.
    """
    if category and category not in _VALID_CATEGORIES:
        category = None
    rows = _read_memories_via_sdk(category=category, limit=30)
    if not rows:
        return "I haven't saved any memories yet, sir."
    bullets = "\n  - ".join(
        f"[{r['category']}] {r['content']}" for r in rows
    )
    return f"What I remember, sir:\n  - {bullets}"


# ── System-prompt injection (called per-turn from jarvis_agent) ─────


def format_memories_for_prompt(top_n: int | None = None) -> str:
    """Render top-N memories as a system-prompt block. Empty string
    when nothing is saved (so the system prompt stays clean for new
    users).

    Side effect: bumps use_count + last_used_ts for each memory
    included so heavily-referenced memories rise.
    """
    if top_n is None:
        top_n = int(os.environ.get("JARVIS_MEMORY_TOP_N", "30"))
    rows = _read_memories_via_sdk(limit=top_n)
    if not rows:
        return ""
    bullets = "\n".join(
        f"  - [{r['category']}] {r['content']}" for r in rows
    )
    block = (
        "## What you remember about Ulrich\n"
        "(Curated facts. Use them naturally; don't recite them.)\n"
        f"{bullets}"
    )
    try:
        _bump_uses_via_sdk([r["memory_id"] for r in rows])
    except Exception as e:
        logger.warning("[memory] bump failed: %s", e)
    return block


def is_available() -> bool:
    """True if the hub state.db is readable. Otherwise tools won't be
    registered and the system-prompt block stays empty."""
    try:
        from client import HubClient
        HubClient.read_memories_sync(limit=1)
        return True
    except Exception as e:
        logger.warning("[memory] hub unavailable: %s", e)
        return False
