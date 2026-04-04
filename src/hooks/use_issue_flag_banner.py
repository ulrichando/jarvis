"""Issue flag banner for friction detection."""

from __future__ import annotations

import re
import time
from typing import Any, List

EXTERNAL_COMMAND_PATTERNS = [
    re.compile(p) for p in [
        r"\bcurl\b", r"\bwget\b", r"\bssh\b", r"\bkubectl\b",
        r"\bdocker\b", r"\baws\b", r"\bgit\s+push\b", r"\bgit\s+pull\b",
        r"\bgit\s+fetch\b", r"\bgh\s+(pr|issue)\b",
    ]
]

FRICTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^no[,!]\s",
        r"\bthat'?s (wrong|incorrect|not (what|right|correct))\b",
        r"\bnot what I (asked|wanted|meant|said)\b",
        r"\bI (said|asked|wanted|told you|already said)\b",
        r"\bwhy did you\b",
        r"\btry again\b",
        r"\b(undo|revert) (that|this|it|what you)\b",
    ]
]

MIN_SUBMIT_COUNT = 3
COOLDOWN_MS = 30 * 60 * 1000


def is_session_container_compatible(messages: List[dict]) -> bool:
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            if tool_name.startswith("mcp__"):
                return False
            if tool_name == "bash":
                command = block.get("input", {}).get("command", "")
                if any(p.search(command) for p in EXTERNAL_COMMAND_PATTERNS):
                    return False
    return True


def has_friction_signal(messages: List[dict]) -> bool:
    for msg in reversed(messages):
        if msg.get("type") != "user":
            continue
        text = msg.get("text", "") or msg.get("content", "")
        if not text:
            continue
        return any(p.search(text) for p in FRICTION_PATTERNS)
    return False


class IssueFlagBanner:
    """Detects friction signals and shows an issue flag banner.

    Equivalent to useIssueFlagBanner React hook.
    """

    def __init__(self):
        self._last_triggered_at: float = 0
        self._active_for_submit: int = -1

    def should_show(self, messages: List[dict], submit_count: int) -> bool:
        if self._active_for_submit == submit_count:
            return True
        now = time.time() * 1000
        if now - self._last_triggered_at < COOLDOWN_MS:
            return False
        if submit_count < MIN_SUBMIT_COUNT:
            return False
        if not (is_session_container_compatible(messages) and has_friction_signal(messages)):
            return False
        self._last_triggered_at = now
        self._active_for_submit = submit_count
        return True
