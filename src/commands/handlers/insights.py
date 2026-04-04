"""JARVIS Insights & Analysis Commands."""

import os
import json
import logging
from src.commands.registry import command, CommandContext, CommandResult

log = logging.getLogger(__name__)


@command(name="insights", description="Analyze your JARVIS usage history -- sessions, patterns, trends",
         category="core")
async def cmd_insights(ctx: CommandContext) -> CommandResult:
    """Historical session analysis."""
    try:
        from src.agent.stats import get_stats_tracker
        tracker = get_stats_tracker()

        total = tracker.get_total_stats()
        today = tracker.get_today_stats()

        lines = [
            "=== JARVIS INSIGHTS ===\n",
            "**Overall Activity**",
            f"  Days active: {total['days_active']}",
            f"  Total sessions: {total['total_sessions']}",
            f"  Total messages: {total['total_messages']:,}",
            f"  Total tokens: {total['total_tokens']:,}",
            f"  Total tool calls: {total['total_tool_calls']:,}",
            "",
            "**Streaks**",
            f"  Current streak: {total['streak_current']} days",
            f"  Longest streak: {total['streak_longest']} days",
            f"  Peak hour: {total['peak_hour']}:00",
            "",
            "**Averages**",
            f"  Messages/day: {total['avg_messages_per_day']:.1f}",
            f"  Tokens/day: {total['avg_tokens_per_day']:,.0f}",
            "",
            "**Today**",
            f"  Sessions: {today.sessions}",
            f"  Messages: {today.messages}",
            f"  Tool calls: {today.tool_calls}",
            f"  Tokens: {today.tokens_input + today.tokens_output:,}",
        ]

        # Model usage breakdown
        if today.model_usage:
            lines.append("")
            lines.append("**Model Usage (Today)**")
            for model, usage in today.model_usage.items():
                lines.append(f"  {model}: {usage['input'] + usage['output']:,} tokens")

        return CommandResult(text="\n".join(lines))
    except Exception as e:
        return CommandResult(text=f"Error loading insights: {e}", success=False)


@command(name="security-review", aliases=["secreview"],
         description="Security audit of pending changes -- finds vulnerabilities in new code",
         category="security")
async def cmd_security_review(ctx: CommandContext) -> CommandResult:
    """Specialized security review focused on new vulnerabilities only."""
    try:
        from src.agent.git_utils import get_unstaged_diff, get_staged_diff, get_branch_name, get_default_branch, get_diff_from_branch

        # Get the diff to review
        branch = get_branch_name()
        default = get_default_branch()

        if branch and branch != default:
            diff = get_diff_from_branch()
            scope = f"branch '{branch}' vs '{default}'"
        else:
            diff = get_staged_diff() or get_unstaged_diff()
            scope = "pending changes"

        if not diff or not diff.strip():
            return CommandResult(text="No changes to review. Commit or stage some changes first.")

        # Build security-focused prompt
        prompt = f"""Review these code changes for security vulnerabilities ONLY.

Focus on:
- Input validation gaps (SQL injection, XSS, command injection, path traversal)
- Authentication/authorization issues
- Cryptographic weaknesses (hardcoded secrets, weak algorithms)
- Data exposure (PII leaks, debug info in production)
- Dependency vulnerabilities

Rules:
- ONLY report NEW vulnerabilities introduced by these changes
- Do NOT report pre-existing issues in unchanged code
- Rate each finding: CRITICAL / HIGH / MEDIUM / LOW
- Include file path and line number for each finding
- Suggest specific fixes

Changes to review ({scope}):

```diff
{diff[:8000]}
```"""

        if ctx.brain:
            result = await ctx.brain.think(prompt)
            return CommandResult(text=result)
        return CommandResult(text="Brain not available for security review.", success=False)
    except Exception as e:
        return CommandResult(text=f"Security review error: {e}", success=False)


@command(name="pr-comments", aliases=["prcomments"],
         description="Fetch and display GitHub PR comments with context",
         category="git")
async def cmd_pr_comments(ctx: CommandContext) -> CommandResult:
    """Fetch GitHub PR comments."""
    import subprocess

    pr_number = ctx.args.strip() if ctx.args else ""

    if not pr_number:
        # Try to detect current PR
        try:
            result = subprocess.run(
                ["gh", "pr", "view", "--json", "number", "-q", ".number"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                pr_number = result.stdout.strip()
        except Exception:
            pass

    if not pr_number:
        return CommandResult(text="Usage: /pr-comments <PR_NUMBER>\nOr run from a branch with an open PR.")

    try:
        # Fetch PR comments
        result = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
             "--jq", '.[] | "**\\(.user.login)** (\\(.path):\\(.line // .original_line))\\n> \\(.body)\\n"'],
            capture_output=True, text=True, timeout=15,
        )

        # Also fetch issue-level comments
        result2 = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{pr_number}/comments",
             "--jq", '.[] | "**\\(.user.login)**:\\n> \\(.body)\\n"'],
            capture_output=True, text=True, timeout=15,
        )

        output = ""
        if result2.stdout.strip():
            output += f"## PR #{pr_number} -- Comments\n\n{result2.stdout}\n"
        if result.stdout.strip():
            output += f"## Code Review Comments\n\n{result.stdout}"

        if not output.strip():
            return CommandResult(text=f"No comments on PR #{pr_number}.")

        return CommandResult(text=output)
    except FileNotFoundError:
        return CommandResult(text="GitHub CLI (gh) not found. Install: https://cli.github.com/", success=False)
    except Exception as e:
        return CommandResult(text=f"Error fetching PR comments: {e}", success=False)
