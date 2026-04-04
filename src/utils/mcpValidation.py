"""MCP output validation and truncation utilities."""

from __future__ import annotations

from typing import Optional, Union

MCP_TOKEN_COUNT_THRESHOLD_FACTOR = 0.5
IMAGE_TOKEN_ESTIMATE = 1600
DEFAULT_MAX_MCP_OUTPUT_TOKENS = 25000


def get_max_mcp_output_tokens() -> int:
    """Resolve the MCP output token cap from env or default."""
    import os

    env_value = os.environ.get("MAX_MCP_OUTPUT_TOKENS")
    if env_value:
        try:
            parsed = int(env_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_MAX_MCP_OUTPUT_TOKENS


MCPToolResult = Optional[Union[str, list[dict]]]


def _is_text_block(block: dict) -> bool:
    return block.get("type") == "text"


def _is_image_block(block: dict) -> bool:
    return block.get("type") == "image"


def _rough_token_estimation(text: str) -> int:
    """Rough token count: ~4 chars per token."""
    return len(text) // 4


def get_content_size_estimate(content: MCPToolResult) -> int:
    """Estimate the token count for MCP tool result content."""
    if not content:
        return 0
    if isinstance(content, str):
        return _rough_token_estimation(content)

    total = 0
    for block in content:
        if _is_text_block(block):
            total += _rough_token_estimation(block.get("text", ""))
        elif _is_image_block(block):
            total += IMAGE_TOKEN_ESTIMATE
    return total


def _get_max_mcp_output_chars() -> int:
    return get_max_mcp_output_tokens() * 4


def _get_truncation_message() -> str:
    max_tokens = get_max_mcp_output_tokens()
    return (
        f"\n\n[OUTPUT TRUNCATED - exceeded {max_tokens} token limit]\n\n"
        "The tool output was truncated. If this MCP server provides pagination "
        "or filtering tools, use them to retrieve specific portions of the data. "
        "If pagination is not available, inform the user that you are working "
        "with truncated output and results may be incomplete."
    )


def _truncate_string(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars]


def truncate_content_blocks(
    blocks: list[dict], max_chars: int
) -> list[dict]:
    """Truncate content blocks to fit within max_chars."""
    result: list[dict] = []
    current_chars = 0

    for block in blocks:
        if _is_text_block(block):
            remaining = max_chars - current_chars
            if remaining <= 0:
                break
            text = block.get("text", "")
            if len(text) <= remaining:
                result.append(block)
                current_chars += len(text)
            else:
                result.append({"type": "text", "text": text[:remaining]})
                break
        elif _is_image_block(block):
            image_chars = IMAGE_TOKEN_ESTIMATE * 4
            if current_chars + image_chars <= max_chars:
                result.append(block)
                current_chars += image_chars
        else:
            result.append(block)

    return result


def mcp_content_needs_truncation(content: MCPToolResult) -> bool:
    """Check if MCP content needs truncation based on estimated size."""
    if not content:
        return False
    estimate = get_content_size_estimate(content)
    max_tokens = get_max_mcp_output_tokens()
    return estimate > max_tokens * MCP_TOKEN_COUNT_THRESHOLD_FACTOR


def truncate_mcp_content(content: MCPToolResult) -> MCPToolResult:
    """Truncate MCP content and append truncation message."""
    if not content:
        return content

    max_chars = _get_max_mcp_output_chars()
    truncation_msg = _get_truncation_message()

    if isinstance(content, str):
        return _truncate_string(content, max_chars) + truncation_msg
    else:
        truncated = truncate_content_blocks(content, max_chars)
        truncated.append({"type": "text", "text": truncation_msg})
        return truncated


def truncate_mcp_content_if_needed(content: MCPToolResult) -> MCPToolResult:
    """Truncate MCP content only if it exceeds the threshold."""
    if not mcp_content_needs_truncation(content):
        return content
    return truncate_mcp_content(content)
