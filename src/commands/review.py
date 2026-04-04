"""Review command - Review a pull request."""

from __future__ import annotations

from typing import Any


def _local_review_prompt(args: str) -> str:
    return f"""You are an expert code reviewer. Follow these steps:

1. If no PR number is provided in the args, run `gh pr list` to show open PRs
2. If a PR number is provided, run `gh pr view <number>` to get PR details
3. Run `gh pr diff <number>` to get the diff
4. Analyze the changes and provide a thorough code review that includes:
   - Overview of what the PR does
   - Analysis of code quality and style
   - Specific suggestions for improvements
   - Any potential issues or risks

Keep your review concise but thorough. Focus on:
- Code correctness
- Following project conventions
- Performance implications
- Test coverage
- Security considerations

Format your review with clear sections and bullet points.

PR number: {args}"""


async def get_prompt_for_command(args: str = "", **_kwargs: Any) -> list[dict[str, str]]:
    """Return the review prompt."""
    return [{"type": "text", "text": _local_review_prompt(args)}]


review = {
    "type": "prompt",
    "name": "review",
    "description": "Review a pull request",
    "progress_message": "reviewing pull request",
    "content_length": 0,
    "source": "builtin",
    "get_prompt_for_command": get_prompt_for_command,
}
