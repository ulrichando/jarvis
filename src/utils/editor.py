"""
External editor integration.

Provides utilities for launching files in the user's preferred editor,
handling both GUI editors (VS Code, Sublime) and terminal editors (vim, nano).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

# GUI editors that open in a separate window
GUI_EDITORS = [
    "code",
    "cursor",
    "windsurf",
    "codium",
    "subl",
    "atom",
    "gedit",
    "notepad++",
    "notepad",
]

# Editors that accept +N as a goto-line argument
PLUS_N_EDITORS = re.compile(r"\b(vi|vim|nvim|nano|emacs|pico|micro|helix|hx)\b")

# VS Code and forks use -g file:line
VSCODE_FAMILY = {"code", "cursor", "windsurf", "codium"}


def classify_gui_editor(editor: str) -> Optional[str]:
    """
    Classify the editor as GUI or not.

    Returns the matched GUI family name for goto-line argv selection,
    or None for terminal editors.

    Uses basename so /home/alice/code/bin/nvim doesn't match 'code'.
    """
    first_part = editor.split(" ")[0] if editor else ""
    base = Path(first_part).name
    for g in GUI_EDITORS:
        if g in base:
            return g
    return None


def _gui_goto_argv(gui_family: str, file_path: str, line: Optional[int]) -> list[str]:
    """Build goto-line argv for a GUI editor."""
    if not line:
        return [file_path]
    if gui_family in VSCODE_FAMILY:
        return ["-g", f"{file_path}:{line}"]
    if gui_family == "subl":
        return [f"{file_path}:{line}"]
    return [file_path]


def open_file_in_external_editor(file_path: str, line: Optional[int] = None) -> bool:
    """
    Launch a file in the user's external editor.

    For GUI editors: spawns detached so the editor opens in a separate window.
    For terminal editors: blocks until the editor exits.

    Returns True if the editor was launched, False if no editor is available.
    """
    editor = get_external_editor()
    if not editor:
        return False

    parts = editor.split(" ")
    base = parts[0] if parts else editor
    editor_args = parts[1:]
    gui_family = classify_gui_editor(editor)

    if gui_family:
        goto_argv = _gui_goto_argv(gui_family, file_path, line)
        try:
            proc = subprocess.Popen(
                [base] + editor_args + goto_argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Detach - don't wait for the process
        except OSError:
            return False
        return True

    # Terminal editor - blocks until exit
    use_goto_line = line and PLUS_N_EDITORS.search(Path(base).name)
    args = list(editor_args)
    if use_goto_line:
        args.extend([f"+{line}", file_path])
    else:
        args.append(file_path)

    try:
        result = subprocess.run([base] + args)
        return result.returncode == 0
    except OSError:
        return False


@lru_cache(maxsize=1)
def get_external_editor() -> Optional[str]:
    """
    Get the user's preferred external editor.

    Checks VISUAL, then EDITOR environment variables,
    then searches for common editors on PATH.
    """
    visual = os.environ.get("VISUAL", "").strip()
    if visual:
        return visual

    editor = os.environ.get("EDITOR", "").strip()
    if editor:
        return editor

    # Search for available editors in order of preference
    for cmd in ("code", "vi", "nano"):
        if shutil.which(cmd):
            return cmd

    return None
