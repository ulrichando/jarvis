"""
Prompt templates for the background memory extraction agent.
"""

from __future__ import annotations


def _opener(new_message_count: int, existing_memories: str) -> str:
    manifest = ""
    if existing_memories:
        manifest = (
            f"\n\n## Existing memory files\n\n{existing_memories}\n\n"
            "Check this list before writing -- update an existing file rather than creating a duplicate."
        )
    return (
        f"You are now acting as the memory extraction subagent. "
        f"Analyze the most recent ~{new_message_count} messages above "
        f"and use them to update your persistent memory systems.\n\n"
        f"Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail), "
        f"and Edit/Write for paths inside the memory directory only.\n\n"
        f"You have a limited turn budget. Edit requires a prior Read of the same file, "
        f"so the efficient strategy is: turn 1 -- issue all Read calls in parallel; "
        f"turn 2 -- issue all Write/Edit calls in parallel.\n\n"
        f"You MUST only use content from the last ~{new_message_count} messages."
        f"{manifest}"
    )


WHAT_NOT_TO_SAVE = """
## What NOT to save
- Temporary debugging info that won't be useful later
- Exact error messages (save the pattern/solution instead)
- Information that's already in the codebase (comments, docs)
- Extremely specific implementation details that will change
"""


def build_extract_auto_only_prompt(
    new_message_count: int,
    existing_memories: str,
    skip_index: bool = False,
) -> str:
    """Build the extraction prompt for auto-only memory."""
    opener = _opener(new_message_count, existing_memories)

    if skip_index:
        how_to_save = (
            "## How to save memories\n\n"
            "Write each memory to its own file (e.g., `user_role.md`, `feedback_testing.md`).\n\n"
            "- Organize memory semantically by topic, not chronologically\n"
            "- Update or remove memories that turn out to be wrong or outdated\n"
            "- Do not write duplicate memories."
        )
    else:
        how_to_save = (
            "## How to save memories\n\n"
            "Saving a memory is a two-step process:\n\n"
            "**Step 1** -- write the memory to its own file.\n\n"
            "**Step 2** -- add a pointer to that file in `MEMORY.md`.\n\n"
            "- `MEMORY.md` is always loaded into your system prompt\n"
            "- Organize memory semantically by topic, not chronologically\n"
            "- Update or remove memories that turn out to be wrong or outdated\n"
            "- Do not write duplicate memories."
        )

    return f"{opener}\n\n{WHAT_NOT_TO_SAVE}\n\n{how_to_save}"


def build_extract_combined_prompt(
    new_message_count: int,
    existing_memories: str,
    skip_index: bool = False,
) -> str:
    """Build the extraction prompt for combined auto + team memory."""
    return build_extract_auto_only_prompt(new_message_count, existing_memories, skip_index)
