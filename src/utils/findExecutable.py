"""
Executable finder utility.

Find an executable by searching PATH, similar to `which`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutableResult:
    """Result of an executable search."""

    cmd: str
    args: list[str]


def find_executable(exe: str, args: list[str]) -> ExecutableResult:
    """
    Find an executable by searching PATH.

    Returns ExecutableResult where cmd is the resolved path if found,
    or the original name if not. args is always the pass-through of
    the input args.
    """
    resolved = shutil.which(exe)
    return ExecutableResult(cmd=resolved or exe, args=args)
