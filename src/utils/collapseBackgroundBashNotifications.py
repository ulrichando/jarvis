"""Collapse consecutive completed background bash notifications."""

from __future__ import annotations

from typing import Any


def collapse_background_bash_notifications(
    messages: list[dict[str, Any]], verbose: bool = False
) -> list[dict[str, Any]]:
    """Collapse consecutive completed-background-bash task-notifications.

    Failed/killed tasks and agent/workflow notifications are left alone.
    Pass-through in verbose mode.
    """
    if verbose:
        return messages

    result: list[dict[str, Any]] = []
    i = 0

    def _is_completed_bg_bash(msg: dict[str, Any]) -> bool:
        if msg.get("type") != "user":
            return False
        content = msg.get("message", {}).get("content", [])
        if not content or not isinstance(content, list):
            return False
        first = content[0] if content else None
        if not isinstance(first, dict) or first.get("type") != "text":
            return False
        text = first.get("text", "")
        if "<task_notification" not in text:
            return False
        if "completed" not in text:
            return False
        return True

    while i < len(messages):
        msg = messages[i]
        if _is_completed_bg_bash(msg):
            count = 0
            while i < len(messages) and _is_completed_bg_bash(messages[i]):
                count += 1
                i += 1
            if count == 1:
                result.append(msg)
            else:
                result.append({
                    "type": "system",
                    "subtype": "collapsed_bg_bash",
                    "count": count,
                    "message": f"{count} background commands completed",
                })
        else:
            result.append(msg)
            i += 1

    return result
