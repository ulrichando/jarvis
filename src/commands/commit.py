"""Commit command - Create a git commit."""

from __future__ import annotations

import os
from typing import Any, List


ALLOWED_TOOLS = [
    "Bash(git add:*)",
    "Bash(git status:*)",
    "Bash(git commit:*)",
]


def get_prompt_content() -> str:
    """Generate the commit prompt content."""
    from ..utils.attribution import get_attribution_texts
    from ..utils.undercover import get_undercover_instructions, is_undercover

    commit_attribution = get_attribution_texts().get("commit", "")

    prefix = ""
    if os.environ.get("USER_TYPE") == "ant" and is_undercover():
        prefix = get_undercover_instructions() + "\n"

    attribution_line = f"\n\n{commit_attribution}" if commit_attribution else ""

    return f"""{prefix}## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Git Safety Protocol

- NEVER update the git config
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- CRITICAL: ALWAYS create NEW commits. NEVER use git commit --amend, unless the user explicitly requests it
- Do not commit files that likely contain secrets (.env, credentials.json, etc). Warn the user if they specifically request to commit those files
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
- Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported

## Your task

Based on the above changes, create a single git commit:

1. Analyze all staged changes and draft a commit message:
   - Look at the recent commits above to follow this repository's commit message style
   - Summarize the nature of the changes (new feature, enhancement, bug fix, refactoring, test, docs, etc.)
   - Ensure the message accurately reflects the changes and their purpose (i.e. "add" means a wholly new feature, "update" means an enhancement to an existing feature, "fix" means a bug fix, etc.)
   - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what"

2. Stage relevant files and create the commit using HEREDOC syntax:
```
git commit -m "$(cat <<'EOF'
Commit message here.{attribution_line}
EOF
)"
```

You have the capability to call multiple tools in a single response. Stage and create the commit using a single message. Do not use any other tools or do anything else. Do not send any other text or messages besides these tool calls."""


async def get_prompt_for_command(args: str, context: Any) -> List[dict]:
    """Get the prompt for the commit command."""
    from ..utils.prompt_shell_execution import execute_shell_commands_in_prompt

    prompt_content = get_prompt_content()
    final_content = await execute_shell_commands_in_prompt(
        prompt_content, context, "/commit"
    )
    return [{"type": "text", "text": final_content}]


command = {
    "type": "prompt",
    "name": "commit",
    "description": "Create a git commit",
    "allowed_tools": ALLOWED_TOOLS,
    "content_length": 0,
    "progress_message": "creating commit",
    "source": "builtin",
    "get_prompt_for_command": get_prompt_for_command,
}
