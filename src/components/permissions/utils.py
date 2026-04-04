"""Permission utilities.

Logging and tracking for permission events.
"""

from __future__ import annotations
from typing import Any, Optional
import logging
import time

logger = logging.getLogger(__name__)


def logUnaryPermissionEvent(
    tool_name: str,
    decision: str,
    args: dict[str, Any] | None = None,
    duration_ms: float = 0.0,
    source: str = "user",
) -> dict[str, Any]:
    """Log a permission event for analytics and debugging.

    Args:
        tool_name: Name of the tool.
        decision: The decision made ('allow', 'deny', 'always').
        args: Tool arguments (will be sanitized).
        duration_ms: Time taken for the user to decide.
        source: Source of the decision ('user', 'rule', 'auto').

    Returns:
        Dict with the logged event data.
    """
    event = {
        "tool_name": tool_name,
        "decision": decision,
        "source": source,
        "duration_ms": duration_ms,
        "timestamp": time.time(),
    }

    # Sanitize args - don't log full command content or file bodies
    if args:
        safe_args = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 200:
                safe_args[k] = v[:100] + f"... ({len(v)} chars)"
            else:
                safe_args[k] = v
        event["args_summary"] = safe_args

    logger.debug("Permission event: %s %s for %s", decision, tool_name, source)
    return event
