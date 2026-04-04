"""Memory file (CLAUDE.md) loading and management.

Files loaded in order (lowest to highest priority):
1. Managed memory (/etc/claude-code/CLAUDE.md)
2. User memory (~/.claude/CLAUDE.md)
3. Project memory (CLAUDE.md, .claude/CLAUDE.md, .claude/rules/*.md)
4. Local memory (CLAUDE.local.md)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORY_INSTRUCTION_PROMPT = (
    "Codebase and user instructions are shown below. "
    "Be sure to adhere to these instructions. "
    "IMPORTANT: These instructions OVERRIDE any default behavior "
    "and you MUST follow them exactly as written."
)

MAX_MEMORY_CHARACTER_COUNT = 40000

TEXT_FILE_EXTENSIONS = {
    ".md", ".txt", ".text",
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".rs", ".go", ".java", ".c", ".cpp", ".h",
    ".sh", ".bash", ".zsh",
    ".yaml", ".yml", ".toml", ".json",
    ".xml", ".html", ".css", ".scss",
    ".sql", ".graphql",
    ".r", ".rb", ".php", ".swift", ".kt",
}


@dataclass
class MemoryEntry:
    content: str
    source: str
    memory_type: str  # 'managed', 'user', 'project', 'local'
    file_path: Optional[str] = None


async def load_memory_files(cwd: Optional[str] = None) -> list[MemoryEntry]:
    """Load all memory files in priority order."""
    if cwd is None:
        cwd = os.getcwd()

    entries: list[MemoryEntry] = []
    home = str(Path.home())

    # User memory
    user_claude_md = os.path.join(home, ".claude", "CLAUDE.md")
    if os.path.exists(user_claude_md):
        try:
            with open(user_claude_md) as f:
                content = f.read()
            entries.append(MemoryEntry(
                content=content,
                source="user",
                memory_type="user",
                file_path=user_claude_md,
            ))
        except Exception as e:
            logger.debug(f"Failed to read user CLAUDE.md: {e}")

    # Project memory - traverse from cwd to root
    current = cwd
    while True:
        for name in ["CLAUDE.md", os.path.join(".claude", "CLAUDE.md")]:
            path = os.path.join(current, name)
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        content = f.read()
                    entries.append(MemoryEntry(
                        content=content,
                        source="project",
                        memory_type="project",
                        file_path=path,
                    ))
                except Exception as e:
                    logger.debug(f"Failed to read {path}: {e}")

        # Check .claude/rules/*.md
        rules_dir = os.path.join(current, ".claude", "rules")
        if os.path.isdir(rules_dir):
            for fname in sorted(os.listdir(rules_dir)):
                if fname.endswith(".md"):
                    path = os.path.join(rules_dir, fname)
                    try:
                        with open(path) as f:
                            content = f.read()
                        entries.append(MemoryEntry(
                            content=content,
                            source="project",
                            memory_type="project",
                            file_path=path,
                        ))
                    except Exception:
                        pass

        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    # Local memory
    local_md = os.path.join(cwd, "CLAUDE.local.md")
    if os.path.exists(local_md):
        try:
            with open(local_md) as f:
                content = f.read()
            entries.append(MemoryEntry(
                content=content,
                source="local",
                memory_type="local",
                file_path=local_md,
            ))
        except Exception:
            pass

    return entries
