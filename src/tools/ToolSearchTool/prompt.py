"""Prompt for the ToolSearchTool."""
from __future__ import annotations

from src.tools.ToolSearchTool.constants import TOOL_SEARCH_TOOL_NAME

PROMPT_HEAD = """Fetches full schema definitions for deferred tools so they can be called.

"""

PROMPT_TAIL = """ Until fetched, only the name is known -- there is no parameter schema, so the tool cannot be invoked. This tool takes a query, matches it against the deferred tool list, and returns the matched tools' complete JSONSchema definitions. Once a tool's schema appears in that result, it is callable exactly like any tool defined at the top of the prompt.

Query forms:
- "select:Read,Edit,Grep" -- fetch these exact tools by name
- "notebook jupyter" -- keyword search, up to max_results best matches
- "+slack send" -- require "slack" in the name, rank by remaining terms"""


def format_deferred_tool_line(tool_name: str) -> str:
    """Format one deferred-tool line."""
    return tool_name


def get_prompt() -> str:
    """Get the ToolSearch tool prompt."""
    location_hint = "Deferred tools appear by name in system-reminder messages."
    return PROMPT_HEAD + location_hint + PROMPT_TAIL
