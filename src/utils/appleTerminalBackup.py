"""
Apple Terminal.app backup and restore utilities.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Union


@dataclass
class RestoreResultSuccess:
    status: Literal["restored", "no_backup"]


@dataclass
class RestoreResultFailed:
    status: Literal["failed"]
    backup_path: str


RestoreResult = Union[RestoreResultSuccess, RestoreResultFailed]


def get_terminal_plist_path() -> str:
    """Get the path to the Terminal.app preferences plist."""
    return str(Path.home() / "Library" / "Preferences" / "com.apple.Terminal.plist")


def mark_terminal_setup_in_progress(backup_path: str) -> None:
    """Mark that terminal setup is in progress in global config."""
    # Stub: would call save_global_config
    pass


def mark_terminal_setup_complete() -> None:
    """Mark that terminal setup is complete in global config."""
    # Stub: would call save_global_config
    pass


def _get_terminal_recovery_info() -> dict[str, object]:
    """Get terminal recovery info from global config."""
    return {"in_progress": False, "backup_path": None}


async def backup_terminal_preferences() -> Optional[str]:
    """Backup Terminal.app preferences and return the backup path."""
    terminal_plist_path = get_terminal_plist_path()
    backup_path = f"{terminal_plist_path}.bak"

    try:
        result = subprocess.run(
            ["defaults", "export", "com.apple.Terminal", terminal_plist_path],
            capture_output=True,
        )
        if result.returncode != 0:
            return None

        if not os.path.exists(terminal_plist_path):
            return None

        subprocess.run(
            ["defaults", "export", "com.apple.Terminal", backup_path],
            capture_output=True,
        )

        mark_terminal_setup_in_progress(backup_path)
        return backup_path
    except Exception:
        return None


async def check_and_restore_terminal_backup() -> RestoreResult:
    """Check for and restore Terminal.app backup if setup was interrupted."""
    info = _get_terminal_recovery_info()
    in_progress = info.get("in_progress", False)
    backup_path = info.get("backup_path")

    if not in_progress:
        return RestoreResultSuccess(status="no_backup")

    if not backup_path or not isinstance(backup_path, str):
        mark_terminal_setup_complete()
        return RestoreResultSuccess(status="no_backup")

    if not os.path.exists(backup_path):
        mark_terminal_setup_complete()
        return RestoreResultSuccess(status="no_backup")

    try:
        result = subprocess.run(
            ["defaults", "import", "com.apple.Terminal", backup_path],
            capture_output=True,
        )
        if result.returncode != 0:
            return RestoreResultFailed(status="failed", backup_path=backup_path)

        subprocess.run(["killall", "cfprefsd"], capture_output=True)
        mark_terminal_setup_complete()
        return RestoreResultSuccess(status="restored")
    except Exception:
        mark_terminal_setup_complete()
        return RestoreResultFailed(status="failed", backup_path=backup_path)
