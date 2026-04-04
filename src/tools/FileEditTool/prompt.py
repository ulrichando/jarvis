"""Prompt for the FileEditTool."""
from __future__ import annotations
from src.tools.FileEditTool.constants import FILE_EDIT_TOOL_NAME

DESCRIPTION = "Perform exact string replacements in files."

PROMPT = f"""Performs exact string replacements in files.

Usage:
- You must use your `Read` tool at least once in the conversation before editing.
- When editing text from Read tool output, ensure you preserve the exact indentation.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it.
- The edit will FAIL if `old_string` is not unique in the file.
- Use `replace_all` for replacing and renaming strings across the file.
"""
