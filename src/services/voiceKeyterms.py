"""
Voice keyterms for improving STT accuracy.

Provides domain-specific vocabulary hints so the STT engine correctly
recognises coding terminology, project names, and branch names.
"""

from __future__ import annotations

import logging
import os
import re
from os.path import basename
from typing import FrozenSet, List, Optional, Set

logger = logging.getLogger(__name__)

# Global keyterms for coding context
GLOBAL_KEYTERMS: tuple[str, ...] = (
    "MCP",
    "symlink",
    "grep",
    "regex",
    "localhost",
    "codebase",
    "TypeScript",
    "JSON",
    "OAuth",
    "webhook",
    "gRPC",
    "dotfiles",
    "subagent",
    "worktree",
)

MAX_KEYTERMS = 50


def split_identifier(name: str) -> List[str]:
    """Split an identifier (camelCase, kebab-case, snake_case, etc.) into words.

    Fragments of 2 chars or fewer are discarded to avoid noise.
    """
    # Insert space before uppercase letters in camelCase
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Split on various separators
    parts = re.split(r"[-_./\s]+", spaced)
    return [w.strip() for w in parts if 2 < len(w.strip()) <= 20]


def _file_name_words(file_path: str) -> List[str]:
    """Extract words from a filename (without extension)."""
    base = basename(file_path)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return split_identifier(stem)


async def get_voice_keyterms(
    recent_files: Optional[Set[str]] = None,
) -> List[str]:
    """Build a list of keyterms for the voice_stream STT endpoint.

    Combines hardcoded global coding terms with session context
    (project name, git branch, recent files) without any model calls.
    """
    terms: Set[str] = set(GLOBAL_KEYTERMS)

    # Project root basename
    try:
        project_root = os.environ.get("JARVIS_PROJECT_ROOT")
        if project_root:
            name = basename(project_root)
            if 2 < len(name) <= 50:
                terms.add(name)
    except Exception:
        pass

    # Git branch words
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch:
                for word in split_identifier(branch):
                    terms.add(word)
    except Exception:
        pass

    # Recent file names
    if recent_files:
        for file_path in recent_files:
            if len(terms) >= MAX_KEYTERMS:
                break
            for word in _file_name_words(file_path):
                terms.add(word)

    return list(terms)[:MAX_KEYTERMS]
