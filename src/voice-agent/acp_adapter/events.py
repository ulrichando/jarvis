"""Helpers that emit ``session/update`` notifications during a prompt turn.

The adapter holds an ``acp.Client`` connection per process. While a
prompt is running we stream:

  - assistant message deltas (``agent_message_chunk``)
  - thought / reasoning deltas (``agent_thought_chunk``)
  - tool-call start (``tool_call``) and completion (``tool_call_update``)
  - the user's own message, replayed when the IDE asks to see the
    transcript on session/load (``user_message_chunk``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import acp
from acp.schema import AgentPlanUpdate, PlanEntry

logger = logging.getLogger(__name__)


def _json_loads_maybe_prefix(value: str) -> Any:
    """Parse a JSON object even when text trails after the closing brace."""
    text = value.strip()
    try:
        return json.loads(text)
    except Exception:
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(text)
        return data


def build_plan_update_from_todo_result(result: Any) -> AgentPlanUpdate | None:
    """Translate JARVIS's ``todo`` tool result into ACP's native plan update.

    Zed renders plan updates as a first-class task panel; mapping the
    todo tool to it gives the IDE the same view JARVIS uses internally.
    """
    if not isinstance(result, str) or not result.strip():
        return None
    try:
        data = _json_loads_maybe_prefix(result)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("todos"), list):
        return None

    todos = data["todos"]
    if not todos:
        return AgentPlanUpdate(session_update="plan", entries=[])

    status_map = {
        "pending": "pending",
        "in_progress": "in_progress",
        "completed": "completed",
        # ACP plans expose pending/in_progress/completed; cancelled gets
        # tagged in the content and reported as completed so the IDE
        # doesn't drop visible context.
        "cancelled": "completed",
    }
    entries: list[PlanEntry] = []
    for item in todos:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("id") or "").strip()
        if not content:
            continue
        raw_status = str(item.get("status") or "pending").strip()
        status = status_map.get(raw_status, "pending")
        if raw_status == "cancelled":
            content = f"[cancelled] {content}"
        entries.append(PlanEntry(content=content, priority="medium", status=status))

    return AgentPlanUpdate(session_update="plan", entries=entries)


async def send_update(
    conn: acp.Client | None,
    session_id: str,
    update: Any,
) -> bool:
    """Send a session-update notification; return True on success."""
    if conn is None:
        return False
    try:
        await conn.session_update(session_id=session_id, update=update)
        return True
    except Exception:
        logger.warning(
            "Failed to send ACP session update (session=%s)", session_id, exc_info=True
        )
        return False


def send_update_threadsafe(
    conn: acp.Client | None,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    update: Any,
    timeout: float = 5.0,
) -> None:
    """Schedule ``send_update`` from a worker thread, blocking on completion.

    The supervisor turn loop dispatches tool calls in the same event
    loop, but tools that take a long time may run synchronous work in a
    thread; this helper is the bridge they use to push interim updates.
    """
    if conn is None:
        return
    try:
        future = asyncio.run_coroutine_threadsafe(
            send_update(conn, session_id, update),
            loop,
        )
    except Exception:
        logger.debug("Failed to schedule session update", exc_info=True)
        return
    try:
        future.result(timeout=timeout)
    except Exception:
        logger.debug("Session update did not complete in time", exc_info=True)


def make_assistant_text_emitter(
    conn: acp.Client | None,
    session_id: str,
) -> "callable[[str], asyncio.Future[None]]":
    """Return ``async def emit(text)`` that streams an assistant chunk."""

    async def _emit(text: str) -> None:
        if not text or conn is None:
            return
        update = acp.update_agent_message_text(text)
        await send_update(conn, session_id, update)

    return _emit


def make_thought_emitter(
    conn: acp.Client | None,
    session_id: str,
) -> "callable[[str], asyncio.Future[None]]":
    """Return ``async def emit(text)`` that streams a reasoning chunk."""

    async def _emit(text: str) -> None:
        if not text or conn is None:
            return
        update = acp.update_agent_thought_text(text)
        await send_update(conn, session_id, update)

    return _emit
