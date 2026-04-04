"""
Utility for persisting large tool results to disk instead of truncating them.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

TOOL_RESULTS_SUBDIR = "tool-results"
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
TOOL_RESULT_CLEARED_MESSAGE = "[Old tool result content cleared]"

# Preview size in bytes
PREVIEW_SIZE_BYTES = 2000

# Default limits
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000
MAX_TOOL_RESULT_BYTES = 100_000
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000
BYTES_PER_TOKEN = 4


@dataclass
class PersistedToolResult:
    filepath: str
    original_size: int
    is_json: bool
    preview: str
    has_more: bool


@dataclass
class PersistToolResultError:
    error: str


@dataclass
class ContentReplacementState:
    seen_ids: Set[str] = field(default_factory=set)
    replacements: Dict[str, str] = field(default_factory=dict)


@dataclass
class ContentReplacementRecord:
    kind: str  # "tool-result"
    tool_use_id: str
    replacement: str


def format_file_size(size_bytes: int) -> str:
    """Format a file size in bytes to a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def get_persistence_threshold(
    tool_name: str,
    declared_max_result_size_chars: int,
) -> int:
    """Resolve the effective persistence threshold for a tool."""
    if not isinstance(declared_max_result_size_chars, (int, float)):
        return declared_max_result_size_chars
    if declared_max_result_size_chars == float("inf"):
        return declared_max_result_size_chars
    return min(declared_max_result_size_chars, DEFAULT_MAX_RESULT_SIZE_CHARS)


def get_tool_result_path(results_dir: str, tool_id: str, is_json: bool) -> str:
    """Get the filepath where a tool result would be persisted."""
    ext = "json" if is_json else "txt"
    return os.path.join(results_dir, f"{tool_id}.{ext}")


def generate_preview(
    content: str,
    max_bytes: int,
) -> Tuple[str, bool]:
    """Generate a preview of content, truncating at a newline boundary when possible."""
    if len(content) <= max_bytes:
        return content, False

    truncated = content[:max_bytes]
    last_newline = truncated.rfind("\n")

    cut_point = last_newline if last_newline > max_bytes * 0.5 else max_bytes
    return content[:cut_point], True


def build_large_tool_result_message(result: PersistedToolResult) -> str:
    """Build a message for large tool results with preview."""
    message = f"{PERSISTED_OUTPUT_TAG}\n"
    message += f"Output too large ({format_file_size(result.original_size)}). "
    message += f"Full output saved to: {result.filepath}\n\n"
    message += f"Preview (first {format_file_size(PREVIEW_SIZE_BYTES)}):\n"
    message += result.preview
    message += "\n...\n" if result.has_more else "\n"
    message += PERSISTED_OUTPUT_CLOSING_TAG
    return message


async def persist_tool_result(
    content: str,
    tool_use_id: str,
    results_dir: str,
) -> PersistedToolResult | PersistToolResultError:
    """Persist a tool result to disk and return info about the persisted file."""
    is_json = False
    try:
        json.loads(content)
        is_json = True
    except (json.JSONDecodeError, TypeError):
        pass

    os.makedirs(results_dir, exist_ok=True)
    filepath = get_tool_result_path(results_dir, tool_use_id, is_json)

    try:
        # Skip if already written (wx equivalent)
        if not os.path.exists(filepath):
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            logger.debug(f"Persisted tool result to {filepath} ({format_file_size(len(content))})")
    except OSError as e:
        return PersistToolResultError(error=str(e))

    preview, has_more = generate_preview(content, PREVIEW_SIZE_BYTES)

    return PersistedToolResult(
        filepath=filepath,
        original_size=len(content),
        is_json=is_json,
        preview=preview,
        has_more=has_more,
    )


def is_persist_error(result: PersistedToolResult | PersistToolResultError) -> bool:
    """Type guard to check if persist result is an error."""
    return isinstance(result, PersistToolResultError)


def is_tool_result_content_empty(content: Any) -> bool:
    """Check if tool result content is empty or effectively empty."""
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        if len(content) == 0:
            return True
        return all(
            isinstance(block, dict)
            and block.get("type") == "text"
            and (not isinstance(block.get("text"), str) or block["text"].strip() == "")
            for block in content
        )
    return False


def create_content_replacement_state() -> ContentReplacementState:
    """Create a new content replacement state."""
    return ContentReplacementState()


def clone_content_replacement_state(
    source: ContentReplacementState,
) -> ContentReplacementState:
    """Clone replacement state for a cache-sharing fork."""
    return ContentReplacementState(
        seen_ids=set(source.seen_ids),
        replacements=dict(source.replacements),
    )
