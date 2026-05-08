"""Write — full-file write matching claude-code's FileWriteTool.

Read-first invariant for EXISTING files (parity with claude-code): if the
file already exists on disk, it must have been read via the read tool in
this session before write() will overwrite. Prevents the "LLM clobbers
content it never saw" failure mode.

For NEW files (path doesn't exist): no read-first requirement, but parent
directory must exist (we don't auto-mkdir — claude-code doesn't either).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from livekit.agents.llm import function_tool

from tools.file_read import has_been_read, mark_written, read_mtime_ns
from tools.plan_mode import assert_not_plan_mode

logger = logging.getLogger("jarvis.write")


@function_tool
async def write(file_path: str, content: str) -> str:
    """Writes a file to the local filesystem.

    Usage:
      - This tool will overwrite the existing file if there is one at
        the provided path.
      - If this is an existing file, you MUST use the `read` tool first
        to read the file's contents. This tool will fail if you did
        not read the file first.
      - Prefer the `edit` tool for modifying existing files — it only
        sends the diff. Only use `write` to create new files or for
        complete rewrites.
      - NEVER create documentation files (*.md) or README files unless
        explicitly requested by the User.
      - Only use emojis if the user explicitly requests it. Avoid
        writing emojis to files unless asked.
      - file_path must be absolute. Parent directory must already exist
        — create it via `bash mkdir -p` first if needed.

    Args:
        file_path: Absolute path to the file to write.
        content:   Full content of the file. Will overwrite any
                   existing file at this path.
    """
    gate = assert_not_plan_mode("write")
    if gate:
        return gate

    fp = (file_path or "").strip()
    if not fp:
        return "Error: file_path is required."
    if not os.path.isabs(fp):
        return f"Error: file_path must be absolute, got: {fp}"
    if content is None:
        return "Error: content is required (pass empty string for an empty file)."

    p = Path(fp)
    if p.is_dir():
        return f"Error: {fp} is a directory, not a file."

    parent = p.parent
    if not parent.exists():
        return (
            f"Error: parent directory does not exist: {parent}. "
            f"Create it first with `bash mkdir -p {parent}`."
        )

    file_exists = p.exists()

    if file_exists:
        # Read-first invariant.
        if not has_been_read(fp):
            return (
                f"Error: file exists at {fp} but has not been read in "
                f"this session. Use the `read` tool first; if you "
                f"intend to overwrite, the read confirms what you're "
                f"replacing."
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
                f"Re-read the file before overwriting."
            )

    # Soft-warn on .md files. Voice supervisor doesn't have great
    # judgment about when .md is appropriate; we don't BLOCK, but we
    # tag the result so the LLM gets feedback in chat_ctx.
    md_warning = ""
    if fp.endswith(".md") and not file_exists:
        md_warning = (
            " [note: created a new .md file — verify the user actually "
            "asked for documentation; the prompt rules ban gratuitous "
            ".md creation]"
        )

    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        new_mtime = p.stat().st_mtime_ns
    except OSError as e:
        return f"Error: could not write file: {type(e).__name__}: {e}"

    mark_written(fp, new_mtime)

    verb = "Overwrote" if file_exists else "Created"
    n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    logger.info(f"write → {fp} ({verb.lower()}, {n_lines} lines, {len(content)} chars)")
    return f"{verb} {fp} ({n_lines} lines, {len(content)} chars).{md_warning}"
