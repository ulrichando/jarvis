"""Security review command - Complete a security review of pending changes."""

from __future__ import annotations

from typing import Any

from .create_moved_to_plugin_command import create_moved_to_plugin_command

SECURITY_REVIEW_MARKDOWN = """---
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git log:*), Bash(git show:*), Bash(git remote show:*), Read, Glob, Grep, LS, Task
description: Complete a security review of the pending changes on the current branch
---

You are a senior security engineer conducting a focused security review of the changes on this branch.

Review the complete diff. This contains all code changes in the PR.

OBJECTIVE:
Perform a security-focused code review to identify HIGH-CONFIDENCE security vulnerabilities that could have real exploitation potential.

CRITICAL INSTRUCTIONS:
1. MINIMIZE FALSE POSITIVES: Only flag issues where you're >80% confident of actual exploitability
2. AVOID NOISE: Skip theoretical issues, style concerns, or low-impact findings
3. FOCUS ON IMPACT: Prioritize vulnerabilities that could lead to unauthorized access, data breaches, or system compromise

SECURITY CATEGORIES TO EXAMINE:
- Input Validation Vulnerabilities (SQL injection, command injection, XXE, template injection, path traversal)
- Authentication & Authorization Issues (auth bypass, privilege escalation, session management)
- Crypto & Secrets Management (hardcoded keys, weak crypto, improper key storage)
- Injection & Code Execution (RCE via deserialization, eval injection, XSS)
- Data Exposure (sensitive data logging, PII handling, API endpoint leakage)

REQUIRED OUTPUT FORMAT:
Output findings in markdown with file, line number, severity, category, description, exploit scenario, and fix recommendation.

SEVERITY GUIDELINES:
- HIGH: Directly exploitable vulnerabilities leading to RCE, data breach, or authentication bypass
- MEDIUM: Vulnerabilities requiring specific conditions but with significant impact
- LOW: Defense-in-depth issues or lower-impact vulnerabilities"""


async def _get_prompt(args: str = "", context: Any = None, **_kwargs: Any) -> list[dict[str, str]]:
    """Return the security review prompt."""
    return [{"type": "text", "text": SECURITY_REVIEW_MARKDOWN}]


command = create_moved_to_plugin_command(
    name="security-review",
    description="Complete a security review of the pending changes on the current branch",
    progress_message="analyzing code changes for security risks",
    plugin_name="security-review",
    plugin_command="security-review",
    get_prompt_while_marketplace_is_private=_get_prompt,
)
