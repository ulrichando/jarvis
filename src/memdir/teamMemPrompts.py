"""Team memory prompt templates."""

from __future__ import annotations

from typing import Optional


def build_team_memory_prompt(team_name: str, memories: list[str]) -> str:
    """Build a system prompt section from team memories."""
    if not memories:
        return ""
    mem_text = "\n\n".join(memories)
    return f"# Team Memory ({team_name})\n\n{mem_text}"
