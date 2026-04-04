"""
Windows path conversion utilities.

Provides conversions between Windows and POSIX path formats.
"""

import os
import re
from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=500)
def windows_path_to_posix_path(windows_path: str) -> str:
    """Convert a Windows path to a POSIX path using pure Python."""
    # Handle UNC paths: \\\\server\\share -> //server/share
    if windows_path.startswith("\\\\"):
        return windows_path.replace("\\", "/")

    # Handle drive letter paths: C:\\Users\\foo -> /c/Users/foo
    match = re.match(r"^([A-Za-z]):[/\\]", windows_path)
    if match:
        drive_letter = match.group(1).lower()
        return "/" + drive_letter + windows_path[2:].replace("\\", "/")

    # Already POSIX or relative -- just flip slashes
    return windows_path.replace("\\", "/")


@lru_cache(maxsize=500)
def posix_path_to_windows_path(posix_path: str) -> str:
    """Convert a POSIX path to a Windows path using pure Python."""
    # Handle UNC paths: //server/share -> \\\\server\\share
    if posix_path.startswith("//"):
        return posix_path.replace("/", "\\")

    # Handle /cygdrive/c/... format
    cygdrive_match = re.match(r"^/cygdrive/([A-Za-z])(/|$)", posix_path)
    if cygdrive_match:
        drive_letter = cygdrive_match.group(1).upper()
        rest = posix_path[len("/cygdrive/" + cygdrive_match.group(1)):]
        return drive_letter + ":" + (rest or "\\").replace("/", "\\")

    # Handle /c/... format (MSYS2/Git Bash)
    drive_match = re.match(r"^/([A-Za-z])(/|$)", posix_path)
    if drive_match:
        drive_letter = drive_match.group(1).upper()
        rest = posix_path[2:]
        return drive_letter + ":" + (rest or "\\").replace("/", "\\")

    # Already Windows or relative -- just flip slashes
    return posix_path.replace("/", "\\")


def get_platform() -> str:
    """Get the current platform identifier."""
    import sys
    if sys.platform == "win32":
        return "windows"
    elif sys.platform == "darwin":
        return "macos"
    return "linux"


def set_shell_if_windows() -> None:
    """
    If Windows, set the SHELL environment variable to git-bash path.
    Used by BashTool for user shell commands.
    """
    if get_platform() == "windows":
        git_bash_path = find_git_bash_path()
        if git_bash_path:
            os.environ["SHELL"] = git_bash_path


def find_git_bash_path() -> Optional[str]:
    """Find the path where bash.exe included with git-bash exists."""
    custom_path = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if custom_path and os.path.exists(custom_path):
        return custom_path

    # Check common installation locations
    for location in [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]:
        if os.path.exists(location):
            return location

    return None
