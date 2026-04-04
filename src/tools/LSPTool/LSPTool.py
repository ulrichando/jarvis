"""LSPTool -- Language Server Protocol integration."""
from __future__ import annotations

from typing import Any, Optional

from src.tools.LSPTool.prompt import LSP_TOOL_NAME
from src.tools.LSPTool.schemas import LSPInput, LSPOutput


async def execute_lsp(input_: LSPInput) -> LSPOutput:
    """Execute an LSP action. Stub for JARVIS."""
    return LSPOutput(
        success=False,
        error="LSP tool not implemented in JARVIS context",
    )
