"""
Shared event metadata enrichment for analytics systems.

Provides a single source of truth for collecting and formatting
event metadata across all analytics systems.
"""

from __future__ import annotations

import json
import os
import platform
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from os.path import basename, splitext
from typing import Any, Literal, Optional


def sanitize_tool_name_for_analytics(tool_name: str) -> str:
    """Redact MCP tool names while preserving built-in tool names."""
    if tool_name.startswith("mcp__"):
        return "mcp_tool"
    return tool_name


def extract_mcp_tool_details(tool_name: str) -> Optional[dict[str, str]]:
    """Extract MCP server and tool names from a full MCP tool name.

    MCP tool names follow the format: mcp__<server>__<tool>
    """
    if not tool_name.startswith("mcp__"):
        return None

    parts = tool_name.split("__")
    if len(parts) < 3:
        return None

    server_name = parts[1]
    mcp_tool_name = "__".join(parts[2:])

    if not server_name or not mcp_tool_name:
        return None

    return {"server_name": server_name, "mcp_tool_name": mcp_tool_name}


def extract_skill_name(tool_name: str, input_data: Any) -> Optional[str]:
    """Extract skill name from Skill tool input."""
    if tool_name != "Skill":
        return None

    if isinstance(input_data, dict) and isinstance(input_data.get("skill"), str):
        return input_data["skill"]

    return None


TOOL_INPUT_STRING_TRUNCATE_AT = 512
TOOL_INPUT_STRING_TRUNCATE_TO = 128
TOOL_INPUT_MAX_JSON_CHARS = 4 * 1024
TOOL_INPUT_MAX_COLLECTION_ITEMS = 20
TOOL_INPUT_MAX_DEPTH = 2


def _truncate_tool_input_value(value: Any, depth: int = 0) -> Any:
    """Truncate tool input values for analytics."""
    if isinstance(value, str):
        if len(value) > TOOL_INPUT_STRING_TRUNCATE_AT:
            return f"{value[:TOOL_INPUT_STRING_TRUNCATE_TO]}...[{len(value)} chars]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth >= TOOL_INPUT_MAX_DEPTH:
        return "<nested>"
    if isinstance(value, list):
        mapped = [_truncate_tool_input_value(v, depth + 1)
                  for v in value[:TOOL_INPUT_MAX_COLLECTION_ITEMS]]
        if len(value) > TOOL_INPUT_MAX_COLLECTION_ITEMS:
            mapped.append(f"...[{len(value)} items]")
        return mapped
    if isinstance(value, dict):
        entries = [(k, v) for k, v in value.items() if not k.startswith("_")]
        mapped = {k: _truncate_tool_input_value(v, depth + 1)
                  for k, v in entries[:TOOL_INPUT_MAX_COLLECTION_ITEMS]}
        if len(entries) > TOOL_INPUT_MAX_COLLECTION_ITEMS:
            mapped["..."] = f"{len(entries)} keys"
        return mapped
    return str(value)


def extract_tool_input_for_telemetry(input_data: Any) -> Optional[str]:
    """Serialize tool input arguments for telemetry.

    Truncates long strings and deep nesting.
    """
    truncated = _truncate_tool_input_value(input_data)
    result = json.dumps(truncated, default=str)
    if len(result) > TOOL_INPUT_MAX_JSON_CHARS:
        result = result[:TOOL_INPUT_MAX_JSON_CHARS] + "...[truncated]"
    return result


MAX_FILE_EXTENSION_LENGTH = 10


def get_file_extension_for_analytics(file_path: str) -> Optional[str]:
    """Extract and sanitize a file extension for analytics logging."""
    _, ext = splitext(file_path)
    ext = ext.lower()
    if not ext or ext == ".":
        return None
    extension = ext[1:]  # Remove leading dot
    if len(extension) > MAX_FILE_EXTENSION_LENGTH:
        return "other"
    return extension


FILE_COMMANDS = {
    "rm", "mv", "cp", "touch", "mkdir", "chmod", "chown",
    "cat", "head", "tail", "sort", "stat", "diff", "wc",
    "grep", "rg", "sed",
}

COMPOUND_OPERATOR_REGEX = re.compile(r"\s*(?:&&|\|\||[;|])\s*")
WHITESPACE_REGEX = re.compile(r"\s+")


def get_file_extensions_from_bash_command(
    command: str,
    simulated_sed_edit_file_path: Optional[str] = None,
) -> Optional[str]:
    """Extract file extensions from a bash command for analytics."""
    if "." not in command and not simulated_sed_edit_file_path:
        return None

    result: Optional[str] = None
    seen: set[str] = set()

    if simulated_sed_edit_file_path:
        ext = get_file_extension_for_analytics(simulated_sed_edit_file_path)
        if ext:
            seen.add(ext)
            result = ext

    for subcmd in COMPOUND_OPERATOR_REGEX.split(command):
        if not subcmd:
            continue
        tokens = WHITESPACE_REGEX.split(subcmd)
        if len(tokens) < 2:
            continue

        first_token = tokens[0]
        slash_idx = first_token.rfind("/")
        base_cmd = first_token[slash_idx + 1:] if slash_idx >= 0 else first_token
        if base_cmd not in FILE_COMMANDS:
            continue

        for arg in tokens[1:]:
            if arg.startswith("-"):
                continue
            ext = get_file_extension_for_analytics(arg)
            if ext and ext not in seen:
                seen.add(ext)
                result = f"{result},{ext}" if result else ext

    return result


@dataclass
class EnvContext:
    """Environment context metadata."""
    platform_name: str = ""
    arch: str = ""
    python_version: str = ""
    terminal: Optional[str] = None
    is_ci: bool = False
    version: str = ""
    build_time: str = ""


@dataclass
class EventMetadata:
    """Core event metadata shared across all analytics systems."""
    model: str = ""
    session_id: str = ""
    user_type: str = ""
    betas: Optional[str] = None
    env_context: dict[str, Any] = field(default_factory=dict)
    is_interactive: str = "true"
    client_type: str = ""


@lru_cache(maxsize=1)
def _build_env_context() -> dict[str, Any]:
    """Build the environment context object."""
    return {
        "platform": platform.system().lower(),
        "arch": platform.machine(),
        "python_version": platform.python_version(),
        "terminal": os.environ.get("TERM"),
        "is_ci": bool(os.environ.get("CI")),
        "version": os.environ.get("JARVIS_VERSION", "0.0.0"),
        "build_time": "",
    }


async def get_event_metadata(
    model: Any = None,
    betas: Any = None,
    additional_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Get core event metadata shared across all analytics systems."""
    model_str = str(model) if model else ""
    env_context = _build_env_context()

    metadata: dict[str, Any] = {
        "model": model_str,
        "session_id": os.environ.get("JARVIS_SESSION_ID", ""),
        "user_type": os.environ.get("USER_TYPE", ""),
        "env_context": env_context,
        "is_interactive": "true",
        "client_type": "",
    }

    if isinstance(betas, str) and betas:
        metadata["betas"] = betas

    return metadata
