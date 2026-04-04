"""
Utility functions for the BashTool.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Optional


def strip_empty_lines(content: str) -> str:
    """Strips leading and trailing lines that contain only whitespace/newlines.
    Unlike strip(), this preserves whitespace within content lines and only removes
    completely empty lines from the beginning and end.
    """
    lines = content.split("\n")

    start_index = 0
    while start_index < len(lines) and lines[start_index].strip() == "":
        start_index += 1

    end_index = len(lines) - 1
    while end_index >= 0 and lines[end_index].strip() == "":
        end_index -= 1

    if start_index > end_index:
        return ""

    return "\n".join(lines[start_index:end_index + 1])


def is_image_output(content: str) -> bool:
    """Check if content is a base64 encoded image data URL."""
    return bool(re.match(r"^data:image/[a-z0-9.+_-]+;base64,", content, re.IGNORECASE))


_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$")


def parse_data_uri(s: str) -> Optional[dict[str, str]]:
    """Parse a data-URI string into its media type and base64 payload."""
    match = _DATA_URI_RE.match(s.strip())
    if not match:
        return None
    return {"media_type": match.group(1), "data": match.group(2)}


def build_image_tool_result(
    stdout: str,
    tool_use_id: str,
) -> Optional[dict[str, Any]]:
    """Build an image tool_result block from shell stdout containing a data URI.
    Returns None if parse fails so callers can fall through to text handling.
    """
    parsed = parse_data_uri(stdout)
    if not parsed:
        return None
    return {
        "tool_use_id": tool_use_id,
        "type": "tool_result",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": parsed["media_type"],
                    "data": parsed["data"],
                },
            }
        ],
    }


MAX_IMAGE_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


def _count_char(s: str, ch: str, start: int = 0) -> int:
    """Count occurrences of a character in s, optionally starting from `start`."""
    return s.count(ch, start)


@dataclass
class FormatOutputResult:
    total_lines: int
    truncated_content: str
    is_image: bool = False


def format_output(content: str, max_output_length: int = 16384) -> FormatOutputResult:
    """Format and optionally truncate shell output."""
    is_image = is_image_output(content)
    if is_image:
        return FormatOutputResult(
            total_lines=1,
            truncated_content=content,
            is_image=True,
        )

    if len(content) <= max_output_length:
        return FormatOutputResult(
            total_lines=content.count("\n") + 1,
            truncated_content=content,
            is_image=False,
        )

    truncated_part = content[:max_output_length]
    remaining_lines = content.count("\n", max_output_length) + 1
    truncated = f"{truncated_part}\n\n... [{remaining_lines} lines truncated] ..."

    return FormatOutputResult(
        total_lines=content.count("\n") + 1,
        truncated_content=truncated,
        is_image=False,
    )


def create_content_summary(content: list[dict[str, Any]]) -> str:
    """Creates a human-readable summary of structured content blocks."""
    parts: list[str] = []
    text_count = 0
    image_count = 0

    for block in content:
        if block.get("type") == "image":
            image_count += 1
        elif block.get("type") == "text" and "text" in block:
            text_count += 1
            preview = block["text"][:200]
            parts.append(preview + ("..." if len(block["text"]) > 200 else ""))

    summary: list[str] = []
    if image_count > 0:
        word = "image" if image_count == 1 else "images"
        summary.append(f"[{image_count} {word}]")
    if text_count > 0:
        word = "block" if text_count == 1 else "blocks"
        summary.append(f"[{text_count} text {word}]")

    result = f"MCP Result: {', '.join(summary)}"
    if parts:
        result += "\n\n" + "\n\n".join(parts)
    return result
