"""Collapse consecutive hook summary messages with the same label."""

from __future__ import annotations

from typing import Any


def _is_labeled_hook_summary(msg: dict[str, Any]) -> bool:
    return (
        msg.get("type") == "system"
        and msg.get("subtype") == "stop_hook_summary"
        and "hookLabel" in msg
    )


def collapse_hook_summaries(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse consecutive hook summaries with the same hookLabel into one."""
    result: list[dict[str, Any]] = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        if _is_labeled_hook_summary(msg):
            label = msg.get("hookLabel")
            group = []
            while i < len(messages):
                nxt = messages[i]
                if not _is_labeled_hook_summary(nxt) or nxt.get("hookLabel") != label:
                    break
                group.append(nxt)
                i += 1
            if len(group) == 1:
                result.append(msg)
            else:
                merged = dict(msg)
                merged["hookCount"] = sum(m.get("hookCount", 0) for m in group)
                merged["hookInfos"] = [
                    info for m in group for info in m.get("hookInfos", [])
                ]
                merged["hookErrors"] = [
                    err for m in group for err in m.get("hookErrors", [])
                ]
                merged["preventedContinuation"] = any(
                    m.get("preventedContinuation") for m in group
                )
                merged["hasOutput"] = any(m.get("hasOutput") for m in group)
                merged["totalDurationMs"] = max(
                    m.get("totalDurationMs", 0) for m in group
                )
                result.append(merged)
        else:
            result.append(msg)
            i += 1

    return result
