"""Computer use common utilities."""

from __future__ import annotations

import os
from typing import Optional

COMPUTER_USE_MCP_SERVER_NAME = "computer-use"
CLI_HOST_BUNDLE_ID = "com.anthropic.claude-code.cli-no-window"

TERMINAL_BUNDLE_ID_FALLBACK: dict[str, str] = {
    "iTerm.app": "com.googlecode.iterm2",
    "Apple_Terminal": "com.apple.Terminal",
    "ghostty": "com.mitchellh.ghostty",
    "kitty": "net.kovidgoyal.kitty",
    "WarpTerminal": "dev.warp.Warp-Stable",
    "vscode": "com.microsoft.VSCode",
}

CLI_CU_CAPABILITIES = {
    "screenshotFiltering": "native",
    "platform": "darwin",
}


def get_terminal_bundle_id() -> Optional[str]:
    """Get the bundle ID of the terminal emulator we're running inside."""
    cf_bundle = os.environ.get("__CFBundleIdentifier")
    if cf_bundle:
        return cf_bundle
    terminal = os.environ.get("TERM_PROGRAM", "")
    return TERMINAL_BUNDLE_ID_FALLBACK.get(terminal)


def is_computer_use_mcp_server(name: str) -> bool:
    """Check if a name matches the computer use MCP server."""
    return name == COMPUTER_USE_MCP_SERVER_NAME
