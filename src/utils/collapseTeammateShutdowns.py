"""Collapse consecutive teammate shutdown attachments."""

from __future__ import annotations

from typing import Any


def _is_teammate_shutdown(msg: dict[str, Any]) -> bool:
    if msg.get("type") != "attachment":
        return False
    att = msg.get("attachment", {})
    return (
        att.get("type") == "task_status"
        and att.get("taskType") == "in_process_teammate"
        and att.get("status") == "completed"
    )


def collapse_teammate_shutdowns(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse consecutive in-process teammate shutdown attachments."""
    result: list[dict[str, Any]] = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        if _is_teammate_shutdown(msg):
            count = 0
            while i < len(messages) and _is_teammate_shutdown(messages[i]):
                count += 1
                i += 1
            if count == 1:
                result.append(msg)
            else:
                result.append({
                    "type": "attachment",
                    "uuid": msg.get("uuid"),
                    "timestamp": msg.get("timestamp"),
                    "attachment": {
                        "type": "teammate_shutdown_batch",
                        "count": count,
                    },
                })
        else:
            result.append(msg)
            i += 1

    return result
