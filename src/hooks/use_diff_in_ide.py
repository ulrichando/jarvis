"""Show file diffs in the connected IDE."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Optional


@dataclass
class FileEdit:
    old_text: str = ""
    new_text: str = ""


@dataclass
class DiffInIDEResult:
    showing_diff_in_ide: bool = False
    ide_name: str = "IDE"
    has_error: bool = False
    close_tab_fn: Optional[Callable] = None

    def close_tab_in_ide(self) -> None:
        if self.close_tab_fn:
            self.close_tab_fn()


def compute_edits_from_contents(
    file_path: str,
    old_content: str,
    new_content: str,
    edit_mode: str = "single",
) -> List[FileEdit]:
    """Re-compute edits from old and new contents.

    Necessary to apply any edits the user may have made to the new contents.
    """
    if old_content == new_content:
        return []
    return [FileEdit(old_text=old_content, new_text=new_content)]


class DiffInIDE:
    """Manages showing file diffs in a connected IDE.

    Done if: tab closed, tab saved, user selected option in IDE or terminal.

    Equivalent to useDiffInIDE React hook.
    """

    def __init__(
        self,
        file_path: str,
        edits: List[FileEdit],
        edit_mode: str = "single",
        on_change: Optional[Callable] = None,
        has_ide_access: bool = False,
        ide_name: str = "IDE",
    ):
        self.file_path = file_path
        self.edits = edits
        self.edit_mode = edit_mode
        self._on_change = on_change
        self._has_ide_access = has_ide_access
        self.ide_name = ide_name
        self._sha = uuid.uuid4().hex[:6]
        self._tab_name = f"[JARVIS] {os.path.basename(file_path)} ({self._sha})"
        self.has_error = False
        self.showing_diff_in_ide = has_ide_access

    def get_result(self) -> DiffInIDEResult:
        return DiffInIDEResult(
            showing_diff_in_ide=self.showing_diff_in_ide and not self.has_error,
            ide_name=self.ide_name,
            has_error=self.has_error,
        )
