"""Init command - Initialize CLAUDE.md file with codebase documentation."""

from __future__ import annotations

import os
from typing import Any

OLD_INIT_PROMPT = """Please analyze this codebase and create a CLAUDE.md file, which will be given to future instances of JARVIS to operate in this repository.

What to add:
1. Commands that will be commonly used, such as how to build, lint, and run tests. Include the necessary commands to develop in this codebase, such as how to run a single test.
2. High-level code architecture and structure so that future instances can be productive more quickly. Focus on the "big picture" architecture that requires reading multiple files to understand.

Usage notes:
- If there's already a CLAUDE.md, suggest improvements to it.
- When you make the initial CLAUDE.md, do not repeat yourself and do not include obvious instructions like "Provide helpful error messages to users", "Write unit tests for all new utilities", "Never include sensitive information (API keys, tokens) in code or commits".
- Avoid listing every component or file structure that can be easily discovered.
- Don't include generic development practices.
- If there are Cursor rules (in .cursor/rules/ or .cursorrules) or Copilot rules (in .github/copilot-instructions.md), make sure to include the important parts.
- If there is a README.md, make sure to include the important parts.
- Do not make up information such as "Common Development Tasks", "Tips for Development", "Support and Documentation" unless this is expressly included in other files that you read.
- Be sure to prefix the file with the following text:

```
# CLAUDE.md

This file provides guidance to JARVIS (claude.ai/code) when working with code in this repository.
```"""

NEW_INIT_PROMPT = """Set up a minimal CLAUDE.md (and optionally skills and hooks) for this repo. CLAUDE.md is loaded into every JARVIS session, so it must be concise - only include what Claude would get wrong without it.

## Phase 1: Ask what to set up

Use AskUserQuestion to find out what the user wants:

- "Which CLAUDE.md files should /init set up?"
  Options: "Project CLAUDE.md" | "Personal CLAUDE.local.md" | "Both project + personal"

- "Also set up skills and hooks?"
  Options: "Skills + hooks" | "Skills only" | "Hooks only" | "Neither, just CLAUDE.md"

## Phase 2: Explore the codebase

Launch a subagent to survey the codebase, and ask it to read key files to understand the project.

## Phase 3: Fill in the gaps

Use AskUserQuestion to gather what you still need to write good CLAUDE.md files and skills.

## Phase 4-8: Write files and summarize

Write CLAUDE.md, CLAUDE.local.md, skills, hooks as requested, then summarize."""


def _is_new_init_enabled() -> bool:
    return (
        os.environ.get("USER_TYPE") == "ant"
        or os.environ.get("CLAUDE_CODE_NEW_INIT", "").lower() in ("1", "true", "yes")
    )


async def get_prompt_for_command(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
    """Return the init prompt."""
    prompt = NEW_INIT_PROMPT if _is_new_init_enabled() else OLD_INIT_PROMPT
    return [{"type": "text", "text": prompt}]


command = {
    "type": "prompt",
    "name": "init",
    "description": "Initialize a new CLAUDE.md file with codebase documentation",
    "content_length": 0,
    "progress_message": "analyzing your codebase",
    "source": "builtin",
    "get_prompt_for_command": get_prompt_for_command,
}
