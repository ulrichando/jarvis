"""Init-verifiers command - Create verifier skills for automated verification."""

from __future__ import annotations

from typing import Any

INIT_VERIFIERS_PROMPT = """Use the TodoWrite tool to track your progress through this multi-step task.

## Goal

Create one or more verifier skills that can be used by the Verify agent to automatically verify code changes in this project or folder.

**Do NOT create verifiers for unit tests or typechecking.** Those are already handled by the standard build/test workflow. Focus on functional verification: web UI (Playwright), CLI (Tmux), and API (HTTP) verifiers.

## Phase 1: Auto-Detection

Analyze the project to detect what's in different subdirectories.

1. **Scan top-level directories** to identify distinct project areas
2. **For each area, detect:** project type/stack, application type, existing verification tools, dev server configuration
3. **Installed verification packages** (for web apps)

## Phase 2: Verification Tool Setup

Based on Phase 1, help the user set up appropriate verification tools.

## Phase 3: Interactive Q&A

Use AskUserQuestion to confirm verifier name and project-specific questions.

## Phase 4: Generate Verifier Skill

Write the skill file to `.jarvis/skills/<verifier-name>/SKILL.md`.

## Phase 5: Confirm Creation

Inform the user about created skills and how the Verify agent discovers them."""


async def get_prompt_for_command(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
    """Return the init-verifiers prompt."""
    return [{"type": "text", "text": INIT_VERIFIERS_PROMPT}]


command = {
    "type": "prompt",
    "name": "init-verifiers",
    "description": "Create verifier skill(s) for automated verification of code changes",
    "content_length": 0,
    "progress_message": "analyzing your project and creating verifier skills",
    "source": "builtin",
    "get_prompt_for_command": get_prompt_for_command,
}
