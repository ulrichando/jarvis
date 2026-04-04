"""API utilities for tool schema management and system prompt handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


CacheScope = Literal["global", "org"]


@dataclass
class SystemPromptBlock:
    text: str
    cache_scope: Optional[CacheScope] = None


SWARM_FIELDS_BY_TOOL: dict[str, list[str]] = {}


def filter_swarm_fields_from_schema(
    tool_name: str, schema: dict[str, Any]
) -> dict[str, Any]:
    """Filter swarm-related fields from a tool's input schema."""
    fields_to_remove = SWARM_FIELDS_BY_TOOL.get(tool_name, [])
    if not fields_to_remove:
        return schema

    filtered = dict(schema)
    props = filtered.get("properties", {})
    if isinstance(props, dict):
        filtered_props = {k: v for k, v in props.items() if k not in fields_to_remove}
        filtered["properties"] = filtered_props
    return filtered


def split_sys_prompt_prefix(
    system_prompt: list[str],
    skip_global_cache: bool = False,
) -> list[SystemPromptBlock]:
    """Split system prompt blocks by content type for cache control."""
    result: list[SystemPromptBlock] = []
    for block in system_prompt:
        if not block:
            continue
        result.append(SystemPromptBlock(text=block, cache_scope="org"))
    return result


def append_system_context(
    system_prompt: list[str], context: dict[str, str]
) -> list[str]:
    """Append system context to system prompt."""
    context_str = "\n".join(f"{k}: {v}" for k, v in context.items())
    return [*system_prompt, context_str] if context_str else list(system_prompt)


def prepend_user_context(
    messages: list[dict[str, Any]], context: dict[str, str]
) -> list[dict[str, Any]]:
    """Prepend user context to messages."""
    if not context:
        return messages

    context_text = "\n".join(f"# {k}\n{v}" for k, v in context.items())
    context_msg = {
        "type": "user",
        "message": {
            "content": f"<system-reminder>\n{context_text}\n</system-reminder>",
        },
        "is_meta": True,
    }
    return [context_msg, *messages]


def normalize_tool_input(tool_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool input before execution."""
    return input_data


def normalize_tool_input_for_api(
    tool_name: str, input_data: dict[str, Any]
) -> dict[str, Any]:
    """Strip fields added by normalize_tool_input before sending to API."""
    return input_data
