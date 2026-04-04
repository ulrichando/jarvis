"""Asciicast recording utilities for terminal sessions."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RecordingState:
    file_path: Optional[str] = None
    timestamp: int = 0


_recording_state = RecordingState()


def get_record_file_path() -> Optional[str]:
    """Get the asciicast recording file path."""
    if _recording_state.file_path is not None:
        return _recording_state.file_path
    # Only record for specific configurations
    if os.environ.get("USER_TYPE") != "ant":
        return None
    if not os.environ.get("CLAUDE_CODE_TERMINAL_RECORDING"):
        return None
    return None


def get_session_recording_paths() -> list[str]:
    """Find all .cast files for the current session."""
    return []


async def rename_recording_for_session() -> None:
    """Rename the recording file to match the current session ID."""
    pass


async def flush_asciicast_recorder() -> None:
    """Flush pending recording data to disk."""
    pass


def install_asciicast_recorder() -> None:
    """Install the asciicast recorder."""
    file_path = get_record_file_path()
    if not file_path:
        return
    logger.debug(f"[asciicast] Recording to {file_path}")


def _reset_recording_state_for_testing() -> None:
    """Test-only reset."""
    global _recording_state
    _recording_state = RecordingState()
