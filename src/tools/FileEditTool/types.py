"""
Types for the FileEditTool.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FileEditInput:
    """Parsed input for a file edit operation."""
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass
class EditInput:
    """Individual edit without file_path."""
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass
class FileEdit:
    """Runtime version where replace_all is always defined."""
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass
class Hunk:
    """A diff hunk."""
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[str] = field(default_factory=list)


@dataclass
class GitDiff:
    """A git diff entry."""
    filename: str
    status: str  # "modified" or "added"
    additions: int
    deletions: int
    changes: int
    patch: str
    repository: Optional[str] = None


@dataclass
class FileEditOutput:
    """Output from a file edit operation."""
    file_path: str
    old_string: str
    new_string: str
    original_file: str
    structured_patch: list[Hunk]
    user_modified: bool
    replace_all: bool
    git_diff: Optional[GitDiff] = None
