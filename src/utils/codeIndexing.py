"""Code indexing tool detection utilities."""

from __future__ import annotations

import re
from typing import Literal, Optional

CodeIndexingTool = Literal[
    "sourcegraph", "hound", "seagoat", "bloop", "gitloop",
    "cody", "aider", "continue", "github-copilot", "cursor",
    "tabby", "codeium", "tabnine", "augment", "windsurf",
    "aide", "pieces", "qodo", "amazon-q", "gemini",
    "claude-context", "code-index-mcp", "local-code-search",
    "autodev-codebase", "openctx",
]

CLI_COMMAND_MAPPING: dict[str, str] = {
    "src": "sourcegraph",
    "cody": "cody",
    "aider": "aider",
    "tabby": "tabby",
    "tabnine": "tabnine",
    "augment": "augment",
    "pieces": "pieces",
    "qodo": "qodo",
    "aide": "aide",
    "hound": "hound",
    "seagoat": "seagoat",
    "bloop": "bloop",
    "gitloop": "gitloop",
    "q": "amazon-q",
    "gemini": "gemini",
}

MCP_SERVER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^sourcegraph$", re.I), "sourcegraph"),
    (re.compile(r"^cody$", re.I), "cody"),
    (re.compile(r"^openctx$", re.I), "openctx"),
]


def detect_code_indexing_from_command(command: str) -> Optional[str]:
    """Detect code indexing tool from a CLI command."""
    first_word = command.strip().split()[0] if command.strip() else ""
    return CLI_COMMAND_MAPPING.get(first_word)


def detect_code_indexing_from_mcp_server(server_name: str) -> Optional[str]:
    """Detect code indexing tool from an MCP server name."""
    for pattern, tool in MCP_SERVER_PATTERNS:
        if pattern.match(server_name):
            return tool
    return None
