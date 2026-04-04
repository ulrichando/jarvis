"""Prompt for the LSPTool."""
from __future__ import annotations

LSP_TOOL_NAME = "LSP"

DESCRIPTION = "Interact with Language Server Protocol servers for code intelligence"

PROMPT = """Interact with Language Server Protocol (LSP) servers for code intelligence features.

Supported actions:
- diagnostics: Get code diagnostics (errors, warnings) for a file
- definition: Go to definition of a symbol
- references: Find all references to a symbol
- hover: Get hover information for a symbol
- completion: Get code completions at a position
"""
