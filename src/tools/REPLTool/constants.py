"""REPL tool constants and mode checks."""
from __future__ import annotations

import os

from src.tools.AgentTool.constants import AGENT_TOOL_NAME
from src.tools.BashTool.toolName import BASH_TOOL_NAME
from src.tools.FileEditTool.constants import FILE_EDIT_TOOL_NAME
from src.tools.FileReadTool.prompt import FILE_READ_TOOL_NAME
from src.tools.FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
from src.tools.GlobTool.prompt import GLOB_TOOL_NAME
from src.tools.GrepTool.prompt import GREP_TOOL_NAME
from src.tools.NotebookEditTool.constants import NOTEBOOK_EDIT_TOOL_NAME

REPL_TOOL_NAME = "REPL"


def _is_env_truthy(val: str | None) -> bool:
    return val is not None and val.lower() in ("1", "true", "yes")


def _is_env_defined_falsy(val: str | None) -> bool:
    return val is not None and val.lower() in ("0", "false", "no")


def is_repl_mode_enabled() -> bool:
    """REPL mode is default-on for ants in the interactive CLI (opt out with
    CLAUDE_CODE_REPL=0). The legacy CLAUDE_REPL_MODE=1 also forces it on.

    SDK entrypoints (sdk-ts, sdk-py, sdk-cli) are NOT defaulted on.
    """
    if _is_env_defined_falsy(os.environ.get("CLAUDE_CODE_REPL")):
        return False
    if _is_env_truthy(os.environ.get("CLAUDE_REPL_MODE")):
        return True
    return (
        os.environ.get("USER_TYPE") == "ant"
        and os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "cli"
    )


# Tools that are only accessible via REPL when REPL mode is enabled.
# When REPL mode is on, these tools are hidden from Claude's direct use,
# forcing Claude to use REPL for batch operations.
REPL_ONLY_TOOLS: frozenset[str] = frozenset([
    FILE_READ_TOOL_NAME,
    FILE_WRITE_TOOL_NAME,
    FILE_EDIT_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    BASH_TOOL_NAME,
    NOTEBOOK_EDIT_TOOL_NAME,
    AGENT_TOOL_NAME,
])
