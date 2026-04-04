"""Memory file detection: identify session files, memory directories, and auto-managed memory."""

from __future__ import annotations

import os
import re
from typing import Literal, Optional

MemoryScope = Literal["personal", "team"]


def _get_config_home_dir() -> str:
    """Get the JARVIS config home directory."""
    return os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))


def _normalize_path(p: str) -> str:
    """Normalize path to forward slashes for comparison."""
    return p.replace("\\", "/")


def detect_session_file_type(
    file_path: str,
) -> Optional[Literal["session_memory", "session_transcript"]]:
    """Detect if a file path is a session-related file under config dir."""
    config_dir = _get_config_home_dir()
    normalized = _normalize_path(file_path)
    config_cmp = _normalize_path(config_dir)

    if not normalized.startswith(config_cmp):
        return None

    if "/session-memory/" in normalized and normalized.endswith(".md"):
        return "session_memory"
    if "/projects/" in normalized and normalized.endswith(".jsonl"):
        return "session_transcript"
    return None


def detect_session_pattern_type(
    pattern: str,
) -> Optional[Literal["session_memory", "session_transcript"]]:
    """Check if a glob/pattern string indicates session file access intent."""
    normalized = pattern.replace("\\", "/")

    if "session-memory" in normalized and (
        ".md" in normalized or normalized.endswith("*")
    ):
        return "session_memory"
    if ".jsonl" in normalized or (
        "projects" in normalized and "*.jsonl" in normalized
    ):
        return "session_transcript"
    return None


def is_auto_managed_memory_file(file_path: str) -> bool:
    """Check if a file is a JARVIS-managed memory file (NOT user-managed instruction files).

    Includes: auto-memory, agent memory, session memory/transcripts.
    Excludes: JARVIS.md, JARVIS.local.md, .jarvis/rules/*.md (user-managed).
    """
    normalized = _normalize_path(file_path)

    # Check for session files
    if detect_session_file_type(file_path) is not None:
        return True

    # Check for agent memory paths
    if "/agent-memory/" in normalized or "/agent-memory-local/" in normalized:
        return True

    # Check for auto-memory (memdir) path
    config_dir = _normalize_path(_get_config_home_dir())
    if normalized.startswith(config_dir) and "/memory/" in normalized:
        return True

    return False


def is_memory_directory(dir_path: str) -> bool:
    """Check if a directory path is a memory-related directory."""
    normalized = _normalize_path(os.path.normpath(dir_path))

    if "/agent-memory/" in normalized or "/agent-memory-local/" in normalized:
        return True

    config_dir = _normalize_path(_get_config_home_dir())

    if not normalized.startswith(config_dir):
        return False

    if "/session-memory/" in normalized:
        return True
    if "/projects/" in normalized:
        return True
    if "/memory/" in normalized:
        return True

    return False


def is_auto_managed_memory_pattern(pattern: str) -> bool:
    """Check if a glob/pattern targets auto-managed memory files only."""
    if detect_session_pattern_type(pattern) is not None:
        return True
    normalized = pattern.replace("\\", "/")
    if "agent-memory/" in normalized or "agent-memory-local/" in normalized:
        return True
    return False


def is_shell_command_targeting_memory(command: str) -> bool:
    """Check if a shell command targets memory files by extracting paths."""
    config_dir = _get_config_home_dir()
    command_lower = command.lower()

    # Quick check: does the command mention the config dir?
    if _normalize_path(config_dir).lower() not in _normalize_path(command_lower):
        return False

    # Extract absolute path-like tokens
    matches = re.findall(r"(?:[A-Za-z]:[/\\]|/)[^\s'\"]+", command)
    if not matches:
        return False

    for match in matches:
        clean_path = re.sub(r"[,;|&>]+$", "", match)
        if is_auto_managed_memory_file(clean_path) or is_memory_directory(clean_path):
            return True

    return False
