"""Edit — exact-string replacement matching claude-code's FileEditTool.

Read-first invariant: the file_path must have been read via the read tool
in this session before edit() will accept changes. That's not arbitrary
discipline — it forces the LLM to ground its old_string on actual file
content (line numbers + indentation), preventing the failure mode where
the LLM imagines an old_string that doesn't quite match.

External-modification check: if the file's mtime changed since the read,
the edit is rejected with a hint to re-read. Catches the user-edited-the-
file-while-the-LLM-was-thinking case.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from livekit.agents.llm import function_tool

from tools.file_read import has_been_read, mark_written, read_mtime_ns
from tools.plan_mode import assert_not_plan_mode

logger = logging.getLogger("jarvis.edit")


@function_tool
async def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Performs exact string replacements in files.

    Usage:
      - You must use the `read` tool at least once in the conversation
        before editing. This tool will error if you attempt an edit
        without reading the file.
      - When editing text from the read tool's output, ensure you
        preserve the exact indentation (tabs/spaces) as it appears
        AFTER the line number prefix. The line number prefix format
        is: line number + tab. Everything after that is the actual
        file content to match. Never include any part of the line
        number prefix in old_string or new_string.
      - ALWAYS prefer editing existing files in the codebase. NEVER
        write new files unless explicitly required.
      - The edit will FAIL if `old_string` is not unique in the file.
        Either provide a larger string with more surrounding context
        to make it unique, or use `replace_all=True` to change every
        instance of `old_string`.
      - Use the smallest old_string that's clearly unique — usually
        2-4 adjacent lines is sufficient. Avoid including 10+ lines
        of context when less uniquely identifies the target.
      - Use `replace_all=True` for replacing/renaming a token across
        a file (e.g. renaming a variable).

    Args:
        file_path:   Absolute path to the file to modify.
        old_string:  The text to replace.
        new_string:  The text to replace it with (must differ from
                     old_string).
        replace_all: Replace every occurrence (default False).
    """
    gate = assert_not_plan_mode("edit")
    if gate:
        return gate

    fp = (file_path or "").strip()
    if not fp:
        return "Error: file_path is required."
    if not os.path.isabs(fp):
        return f"Error: file_path must be absolute, got: {fp}"
    if old_string == new_string:
        return "Error: old_string and new_string are identical — nothing to do."
    if not old_string:
        return "Error: old_string cannot be empty. To create a new file, use the write tool."

    p = Path(fp)
    if not p.exists():
        return f"Error: file does not exist: {fp}. To create a new file, use the write tool."
    if p.is_dir():
        return f"Error: {fp} is a directory, not a file."

    # Read-first invariant.
    if not has_been_read(fp):
        return (
            f"Error: file has not been read in this session. Use the "
            f"`read` tool on {fp} first, then retry the edit."
        )

    # External-modification check.
    try:
        mtime_now = p.stat().st_mtime_ns
    except OSError as e:
        return f"Error: could not stat file: {type(e).__name__}: {e}"
    mtime_at_read = read_mtime_ns(fp)
    if mtime_at_read is not None and mtime_now != mtime_at_read:
        return (
            f"Error: {fp} has changed on disk since you read it. "
            f"Re-read the file and reformulate the edit."
        )

    # Load.
    try:
        with open(p, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as e:
        return f"Error: could not read file: {type(e).__name__}: {e}"
    except UnicodeDecodeError:
        return f"Error: file is not UTF-8 text. Use the bash tool with sed if you need to edit a binary or non-UTF-8 file."

    # Match.
    occurrences = content.count(old_string)
    if occurrences == 0:
        return (
            f"Error: old_string not found in {fp}. "
            f"Re-read the file and check the exact text — including "
            f"whitespace, tabs vs spaces, and line endings."
        )
    if occurrences > 1 and not replace_all:
        return (
            f"Error: old_string matches {occurrences} times in {fp}. "
            f"Either include more surrounding context to make it unique, "
            f"or pass replace_all=True to change every occurrence."
        )

    # Apply.
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    if new_content == content:
        return "Error: replacement produced no change. The match must have been a no-op."

    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        new_mtime = p.stat().st_mtime_ns
    except OSError as e:
        return f"Error: could not write file: {type(e).__name__}: {e}"

    mark_written(fp, new_mtime)

    n_changed = occurrences if replace_all else 1
    plural = "occurrences" if n_changed != 1 else "occurrence"
    logger.info(f"edit → {fp} ({n_changed} {plural} replaced)")
    return f"Edited {fp}: {n_changed} {plural} replaced."
