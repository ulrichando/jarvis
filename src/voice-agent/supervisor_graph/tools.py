"""Tool execution utilities for the supervisor graph.

Direct (non-handoff) tool calls are dispatched by LangGraph's
prebuilt ToolNode. After the ToolNode runs, `clear_resolved_pending`
removes resolved call_ids from `pending_tool_calls`, which lets
speak_gate release.

Specialist handoffs (transfer_to_*) take a different path — they're
intercepted in the graph's branch logic so the specialist sub-graph
runs instead of ToolNode. See `graph.py` for the wiring.
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage


def clear_resolved_pending(state: dict) -> dict:
    """Remove tool_call_ids from pending_tool_calls if a matching
    ToolMessage exists in messages. Idempotent."""
    pending: list[str] = list(state.get("pending_tool_calls") or [])
    if not pending:
        return {"pending_tool_calls": []}
    seen_ids = {
        m.tool_call_id for m in (state.get("messages") or [])
        if isinstance(m, ToolMessage)
    }
    remaining = [p for p in pending if p not in seen_ids]
    return {"pending_tool_calls": remaining}
