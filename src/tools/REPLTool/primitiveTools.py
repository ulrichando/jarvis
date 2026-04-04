"""Primitive tools for the REPL mode."""
from __future__ import annotations

from typing import Any

# In REPL mode, these tools are accessible only through the REPL interface
# rather than being directly available to the model.
PRIMITIVE_TOOL_NAMES = [
    "read_file",
    "write_file",
    "edit_file",
    "search_files",
    "glob_files",
    "bash",
    "notebook_edit",
]
