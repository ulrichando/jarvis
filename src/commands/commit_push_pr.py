"""Commit-push-pr command - Commit, push, and open a PR."""

from __future__ import annotations

import os
from typing import Any, List, Optional


ALLOWED_TOOLS = [
    "Bash(git checkout --branch:*)",
    "Bash(git checkout -b:*)",
    "Bash(git add:*)",
    "Bash(git status:*)",
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr edit:*)",
    "Bash(gh pr view:*)",
    "Bash(gh pr merge:*)",
    "ToolSearch",
    "mcp__slack__send_message",
    "mcp__claude_ai_Slack__slack_send_message",
]


def get_prompt_content(
    default_branch: str,
    pr_attribution: Optional[str] = None,
) -> str:
    """Generate the commit-push-pr prompt content."""
    from ..utils.attribution import get_attribution_texts
    from ..utils.undercover import get_undercover_instructions, is_undercover

    attribution_texts = get_attribution_texts()
    commit_attribution = attribution_texts.commit
    default_pr_attribution = attribution_texts.pr
    effective_pr_attribution = pr_attribution if pr_attribution is not None else default_pr_attribution
    safe_user = os.environ.get("SAFEUSER", "")
    username = os.environ.get("USER", "")

    prefix = ""
    reviewer_arg = " and `--reviewer anthropics/claude-code`"
    add_reviewer_arg = " (and add `--add-reviewer anthropics/claude-code`)"
    changelog_section = """

## Changelog
<!-- CHANGELOG:START -->
[If this PR contains user-facing changes, add a changelog entry here. Otherwise, remove this section.]
<!-- CHANGELOG:END -->"""
    slack_step = """

5. After creating/updating the PR, check if the user's CLAUDE.md mentions posting to Slack channels. If it does, use ToolSearch to search for "slack send message" tools. If ToolSearch finds a Slack tool, ask the user if they'd like you to post the PR URL to the relevant Slack channel. Only post if the user confirms. If ToolSearch returns no results or errors, skip this step silently--do not mention the failure, do not attempt workarounds, and do not try alternative approaches."""

    if os.environ.get("USER_TYPE") == "ant" and is_undercover():
        prefix = get_undercover_instructions() + "\n"
        reviewer_arg = ""
        add_reviewer_arg = ""
        changelog_section = ""
        slack_step = ""

    commit_attr_line = f"\n\n{commit_attribution}" if commit_attribution else ""
    pr_attr_line = f"\n\n{effective_pr_attribution}" if effective_pr_attribution else ""

    return f"""{prefix}## Context

- `SAFEUSER`: {safe_user}
- `whoami`: {username}
- `git status`: !`git status`
- `git diff HEAD`: !`git diff HEAD`
- `git branch --show-current`: !`git branch --show-current`
- `git diff {default_branch}...HEAD`: !`git diff {default_branch}...HEAD`
- `gh pr view --json number 2>/dev/null || true`: !`gh pr view --json number 2>/dev/null || true`

## Git Safety Protocol

- NEVER update the git config
- NEVER run destructive/irreversible git commands (like push --force, hard reset, etc) unless the user explicitly requests them
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- Do not commit files that likely contain secrets (.env, credentials.json, etc)
- Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported

## Your task

Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request from the git diff {default_branch}...HEAD output above).

Based on the above changes:
1. Create a new branch if on {default_branch} (use SAFEUSER from context above for the branch name prefix, falling back to whoami if SAFEUSER is empty, e.g., `username/feature-name`)
2. Create a single commit with an appropriate message using heredoc syntax{", ending with the attribution text shown in the example below" if commit_attribution else ""}:
```
git commit -m "$(cat <<'EOF'
Commit message here.{commit_attr_line}
EOF
)"
```
3. Push the branch to origin
4. If a PR already exists for this branch (check the gh pr view output above), update the PR title and body using `gh pr edit` to reflect the current diff{add_reviewer_arg}. Otherwise, create a pull request using `gh pr create` with heredoc syntax for the body{reviewer_arg}.
   - IMPORTANT: Keep PR titles short (under 70 characters). Use the body for details.
```
gh pr create --title "Short, descriptive title" --body "$(cat <<'EOF'
## Summary
<1-3 bullet points>

## Test plan
[Bulleted markdown checklist of TODOs for testing the pull request...]{changelog_section}{pr_attr_line}
EOF
)"
```

You have the capability to call multiple tools in a single response. You MUST do all of the above in a single message.{slack_step}

Return the PR URL when you're done, so the user can see it."""


async def get_prompt_for_command(args: str, context: Any) -> List[dict]:
    """Get the prompt for the commit-push-pr command."""
    import asyncio
    from ..utils.attribution import get_enhanced_pr_attribution
    from ..utils.git import get_default_branch
    from ..utils.prompt_shell_execution import execute_shell_commands_in_prompt

    default_branch, pr_attribution = await asyncio.gather(
        get_default_branch(),
        get_enhanced_pr_attribution(context.get_app_state),
    )

    prompt_content = get_prompt_content(default_branch, pr_attribution)

    trimmed_args = args.strip() if args else ""
    if trimmed_args:
        prompt_content += f"\n\n## Additional instructions from user\n\n{trimmed_args}"

    final_content = await execute_shell_commands_in_prompt(
        prompt_content, context, "/commit-push-pr"
    )
    return [{"type": "text", "text": final_content}]


command = {
    "type": "prompt",
    "name": "commit-push-pr",
    "description": "Commit, push, and open a PR",
    "allowed_tools": ALLOWED_TOOLS,
    "content_length": len(get_prompt_content("main")),
    "progress_message": "creating commit and PR",
    "source": "builtin",
    "get_prompt_for_command": get_prompt_for_command,
}
