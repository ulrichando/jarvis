"""Validation utilities for add-dir command."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass
class SuccessResult:
    result_type: str = "success"
    absolute_path: str = ""


@dataclass
class EmptyPathResult:
    result_type: str = "emptyPath"


@dataclass
class PathErrorResult:
    result_type: str = ""
    directory_path: str = ""
    absolute_path: str = ""


@dataclass
class AlreadyInWorkingDirResult:
    result_type: str = "alreadyInWorkingDirectory"
    directory_path: str = ""
    working_dir: str = ""


AddDirectoryResult = Union[SuccessResult, EmptyPathResult, PathErrorResult, AlreadyInWorkingDirResult]


async def validate_directory_for_workspace(
    directory_path: str,
    working_directories: list[str] | None = None,
) -> AddDirectoryResult:
    """Validate a directory path for adding to workspace."""
    if not directory_path:
        return EmptyPathResult()

    absolute_path = str(Path(os.path.expanduser(directory_path)).resolve())

    if not os.path.exists(absolute_path):
        return PathErrorResult(
            result_type="pathNotFound",
            directory_path=directory_path,
            absolute_path=absolute_path,
        )

    if not os.path.isdir(absolute_path):
        return PathErrorResult(
            result_type="notADirectory",
            directory_path=directory_path,
            absolute_path=absolute_path,
        )

    if working_directories:
        for working_dir in working_directories:
            if absolute_path.startswith(working_dir):
                return AlreadyInWorkingDirResult(
                    directory_path=directory_path,
                    working_dir=working_dir,
                )

    return SuccessResult(absolute_path=absolute_path)


def add_dir_help_message(result: AddDirectoryResult) -> str:
    """Get a help message for the add directory result."""
    if isinstance(result, EmptyPathResult):
        return "Please provide a directory path."
    elif isinstance(result, PathErrorResult):
        if result.result_type == "pathNotFound":
            return f"Path {result.absolute_path} was not found."
        elif result.result_type == "notADirectory":
            parent_dir = str(Path(result.absolute_path).parent)
            return (
                f"{result.directory_path} is not a directory. "
                f"Did you mean to add the parent directory {parent_dir}?"
            )
    elif isinstance(result, AlreadyInWorkingDirResult):
        return (
            f"{result.directory_path} is already accessible within "
            f"the existing working directory {result.working_dir}."
        )
    elif isinstance(result, SuccessResult):
        return f"Added {result.absolute_path} as a working directory."
    return "Unknown result."
