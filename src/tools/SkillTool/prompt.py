"""Prompt for the SkillTool."""
from __future__ import annotations

# Skill listing gets 1% of the context window (in characters)
SKILL_BUDGET_CONTEXT_PERCENT = 0.01
CHARS_PER_TOKEN = 4
DEFAULT_CHAR_BUDGET = 8_000  # Fallback: 1% of 200k x 4

# Per-entry hard cap
MAX_LISTING_DESC_CHARS = 250


def get_char_budget(context_window_tokens: int | None = None) -> int:
    if context_window_tokens:
        return int(context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT)
    return DEFAULT_CHAR_BUDGET


async def get_prompt(cwd: str = "") -> str:
    return """Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>" (e.g., "/commit", "/review-pr"), they are referring to a skill. Use this tool to invoke it.

How to invoke:
- Use this tool with the skill name and optional arguments
- Examples:
  - `skill: "pdf"` - invoke the pdf skill
  - `skill: "commit", args: "-m 'Fix bug'"` - invoke with arguments
  - `skill: "review-pr", args: "123"` - invoke with arguments

Important:
- Available skills are listed in system-reminder messages in the conversation
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- Do not use this tool for built-in CLI commands (like /help, /clear, etc.)
"""


def clear_prompt_cache() -> None:
    """Clear cached prompt data."""
    pass
