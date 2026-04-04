"""PR Comments command - Get comments from a GitHub pull request."""

from __future__ import annotations

from typing import Any

from ..create_moved_to_plugin_command import create_moved_to_plugin_command

PR_COMMENTS_PROMPT = """You are an AI assistant integrated into a git-based version control system. Your task is to fetch and display comments from a GitHub pull request.

Follow these steps:

1. Use `gh pr view --json number,headRepository` to get the PR number and repository info
2. Use `gh api /repos/{owner}/{repo}/issues/{number}/comments` to get PR-level comments
3. Use `gh api /repos/{owner}/{repo}/pulls/{number}/comments` to get review comments
4. Parse and format all comments in a readable way
5. Return ONLY the formatted comments, with no additional text

Format the comments as:

## Comments

[For each comment thread:]
- @author file.ts#line:
  ```diff
  [diff_hunk from the API response]
  ```
  > quoted comment text

  [any replies indented]

If there are no comments, return "No comments found."
"""


async def _get_prompt(args: str = "", context: Any = None, **_kwargs: Any) -> list[dict[str, str]]:
    text = PR_COMMENTS_PROMPT
    if args:
        text += f"\nAdditional user input: {args}"
    return [{"type": "text", "text": text}]


command = create_moved_to_plugin_command(
    name="pr-comments",
    description="Get comments from a GitHub pull request",
    progress_message="fetching PR comments",
    plugin_name="pr-comments",
    plugin_command="pr-comments",
    get_prompt_while_marketplace_is_private=_get_prompt,
)
