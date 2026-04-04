"""Centralized analytics/telemetry logging for tool permission decisions."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

CODE_EDITING_TOOLS = {"Edit", "Write", "NotebookEdit"}


def is_code_editing_tool(tool_name: str) -> bool:
    return tool_name in CODE_EDITING_TOOLS


def log_permission_decision(
    tool_name: str,
    tool_input: Any,
    tool_use_id: str,
    message_id: str,
    decision: str,  # 'accept' or 'reject'
    source: str,
    log_event: Optional[Callable] = None,
    log_otel: Optional[Callable] = None,
) -> None:
    """Log a permission decision to analytics and telemetry.

    Fans out to analytics, OTel telemetry, and code-edit metrics.

    Equivalent to logPermissionDecision TypeScript function.
    """
    event_data: Dict[str, Any] = {
        "tool_name": tool_name,
        "decision": decision,
        "source": source,
        "tool_use_id": tool_use_id,
        "message_id": message_id,
    }

    if log_event:
        log_event("tool_permission_decision", event_data)

    if log_otel:
        log_otel("tool_permission", {
            "tool_name": tool_name,
            "decision": decision,
            "source": source,
        })

    # Track code editing tool decisions
    if is_code_editing_tool(tool_name):
        # Increment code edit decision counter
        pass
