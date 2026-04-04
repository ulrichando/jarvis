"""
Transcript search utilities.

Provides functions for extracting searchable text from renderable messages,
tool use inputs, and tool results.
"""

import re
import weakref
from typing import Any, Dict, List, Optional, Set

SYSTEM_REMINDER_CLOSE = "</system-reminder>"

# Interrupt messages that render as sentinels
INTERRUPT_MESSAGE = "Interrupted"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "Tool use was interrupted by user"
RENDERED_AS_SENTINEL: Set[str] = {INTERRUPT_MESSAGE, INTERRUPT_MESSAGE_FOR_TOOL_USE}

_search_text_cache: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def tool_use_search_text(input_data: Any) -> str:
    """
    Extract searchable text from a tool invocation input.

    Duck-type strategy: known field names, unknown -> empty.
    Under-count > phantom.
    """
    if not input_data or not isinstance(input_data, dict):
        return ""

    parts: List[str] = []

    # Primary argument fields
    for key in (
        "command", "pattern", "file_path", "path", "prompt",
        "description", "query", "url", "skill",
    ):
        value = input_data.get(key)
        if isinstance(value, str):
            parts.append(value)

    # Array fields
    for key in ("args", "files"):
        value = input_data.get(key)
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            parts.append(" ".join(value))

    return "\n".join(parts)


def tool_result_search_text(result: Any) -> str:
    """
    Extract searchable text from a tool result.

    Known shapes:
    - {stdout, stderr} (Bash/Shell)
    - {content} (Grep)
    - {file: {content}} (Read)
    - {filenames: []} (Grep/Glob)
    - {output} (generic)
    """
    if not result or not isinstance(result, dict):
        return result if isinstance(result, str) else ""

    # Known shapes first
    if isinstance(result.get("stdout"), str):
        err = result.get("stderr", "")
        stderr_str = err if isinstance(err, str) else ""
        return result["stdout"] + ("\n" + stderr_str if stderr_str else "")

    file_data = result.get("file")
    if isinstance(file_data, dict) and isinstance(file_data.get("content"), str):
        return file_data["content"]

    # Known output-field names
    parts: List[str] = []
    for key in ("content", "output", "result", "text", "message"):
        value = result.get(key)
        if isinstance(value, str):
            parts.append(value)

    for key in ("filenames", "lines", "results"):
        value = result.get(key)
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            parts.append("\n".join(value))

    return "\n".join(parts)


def strip_system_reminders(text: str) -> str:
    """Strip <system-reminder> tags and their contents from text."""
    result = text
    open_idx = result.find("<system-reminder>")
    while open_idx >= 0:
        close_idx = result.find(SYSTEM_REMINDER_CLOSE, open_idx)
        if close_idx < 0:
            break
        result = result[:open_idx] + result[close_idx + len(SYSTEM_REMINDER_CLOSE):]
        open_idx = result.find("<system-reminder>")
    return result


def compute_search_text(msg: Dict[str, Any]) -> str:
    """
    Flatten a message to searchable text.

    Args:
        msg: Message dict with 'type' and type-specific content.
    Returns:
        Lowercased searchable text.
    """
    raw = ""
    msg_type = msg.get("type", "")

    if msg_type == "user":
        content = msg.get("message", {}).get("content", "")
        if isinstance(content, str):
            raw = "" if content in RENDERED_AS_SENTINEL else content
        elif isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text not in RENDERED_AS_SENTINEL:
                            parts.append(text)
                    elif block.get("type") == "tool_result":
                        parts.append(tool_result_search_text(
                            msg.get("tool_use_result")
                        ))
            raw = "\n".join(parts)

    elif msg_type == "assistant":
        content = msg.get("message", {}).get("content", [])
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(tool_use_search_text(block.get("input")))
            raw = "\n".join(parts)

    elif msg_type == "attachment":
        attachment = msg.get("attachment", {})
        if attachment.get("type") == "relevant_memories":
            memories = attachment.get("memories", [])
            raw = "\n".join(m.get("content", "") for m in memories)

    # Strip system reminders
    return strip_system_reminders(raw).lower()
