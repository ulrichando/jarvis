"""Git & Code commands — version control, review, and code intelligence."""
import json
import subprocess
from pathlib import Path

from src.commands.registry import command, CommandContext, CommandResult, PermLevel


def _run(cmd: list[str], cwd: str = None, timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"


def _git(*args, cwd: str = None, timeout: int = 30) -> tuple[int, str, str]:
    return _run(["git", *args], cwd=cwd, timeout=timeout)


# ── /init ──────────────────────────────────────────────────────────────

@command("init", description="Initialize .jarvis/ project config in current directory",
         usage="/init", category="git", permission=PermLevel.STANDARD)
async def cmd_init(ctx: CommandContext) -> CommandResult:
    jarvis_dir = Path.cwd() / ".jarvis"
    if jarvis_dir.exists():
        return CommandResult(text=f".jarvis/ already exists at {jarvis_dir}")

    jarvis_dir.mkdir(parents=True)
    settings = {
        "project_name": Path.cwd().name,
        "model": "auto",
        "permissions": "full",
        "plugins": [],
        "ignore": [".git", "__pycache__", "node_modules", ".venv"],
    }
    settings_file = jarvis_dir / "settings.json"
    settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    (jarvis_dir / "plugins").mkdir()
    (jarvis_dir / "skills").mkdir()

    return CommandResult(
        text=f"Initialized .jarvis/ in {Path.cwd()}\n"
             f"  Created settings.json, plugins/, skills/",
    )


# ── /commit ────────────────────────────────────────────────────────────

@command("commit", aliases=["ci"], description="Smart commit: generate message, show diff preview, include attribution",
         usage="/commit [message] [--no-add]", category="git", permission=PermLevel.STANDARD)
async def cmd_commit(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    raw_args = ctx.args.strip()
    no_add = "--no-add" in raw_args
    msg = raw_args.replace("--no-add", "").strip()

    # Check for staged changes first, fall back to unstaged
    rc, staged, _ = _git("diff", "--cached", "--stat")
    rc2, unstaged, _ = _git("diff", "--stat")

    if not staged.strip() and not unstaged.strip():
        return CommandResult(text="Nothing to commit -- working tree clean.", success=False)

    # Auto-stage if nothing staged (unless --no-add)
    if not staged.strip() and not no_add:
        _git("add", "-A")
        _, staged, _ = _git("diff", "--cached", "--stat")

    # Build diff preview
    _, diff_text, _ = _git("diff", "--cached")
    preview_lines = ["Staged Changes Preview", "-" * 40]
    if staged.strip():
        preview_lines.append(staged.strip())
    preview_lines.append("")

    if not msg:
        # Generate message via LLM
        if brain and hasattr(brain, "agent_loop"):
            prompt = (
                "Generate a concise git commit message for these changes. "
                "First line: imperative summary (max 72 chars). "
                "Optional blank line + body for complex changes. "
                "Return ONLY the commit message, no explanation:\n\n" + diff_text[:4000]
            )
            try:
                result = await brain.agent_loop(prompt, max_steps=1)
                msg = result.strip().strip('"').strip("'")
            except Exception:
                msg = "Update files"
        else:
            msg = "Update files"

    # Add Co-Authored-By trailer
    try:
        from src.agent.commit_attribution import get_attribution_tracker
        tracker = get_attribution_tracker()
        trailer = tracker.get_co_author_trailer()
        if trailer and trailer not in msg:
            msg = f"{msg}\n\n{trailer}"
    except ImportError:
        pass

    preview_lines.append(f"Commit message: {msg.splitlines()[0]}")

    rc, out, err = _git("commit", "-m", msg)
    if rc != 0:
        return CommandResult(text=f"Commit failed:\n{err}", success=False)

    preview_lines.append("")
    preview_lines.append(out.strip())
    return CommandResult(text="\n".join(preview_lines))


# ── /pr ────────────────────────────────────────────────────────────────

@command("pr", description="Pull request management: draft, status, comments",
         usage="/pr [create|status|comments <number>] [context]", category="git", permission=PermLevel.STANDARD)
async def cmd_pr(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    args = ctx.args.strip()
    parts = args.split(None, 1)
    action = parts[0].lower() if parts else "create"
    rest = parts[1] if len(parts) > 1 else ""

    _, branch, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()

    # ── /pr comments <number> ──
    if action == "comments":
        pr_number = rest.strip() or ""
        if not pr_number:
            # Try to find PR for current branch via gh
            rc, gh_out, _ = _run(["gh", "pr", "view", "--json", "number", "-q", ".number"], timeout=15)
            if rc == 0 and gh_out.strip():
                pr_number = gh_out.strip()
            else:
                return CommandResult(text="Usage: /pr comments <number> (or push branch first)", success=False)

        rc, comments_json, err = _run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
             "--jq", '.[] | "\\(.user.login): \\(.body[:200])"'],
            timeout=15,
        )
        # Fallback: use gh pr view
        if rc != 0:
            rc, comments_json, err = _run(
                ["gh", "pr", "view", pr_number, "--comments", "--json", "comments",
                 "--jq", '.comments[] | "\\(.author.login): \\(.body[:200])"'],
                timeout=15,
            )
        if rc != 0:
            return CommandResult(text=f"Failed to fetch PR comments: {err}", success=False)
        if not comments_json.strip():
            return CommandResult(text=f"No comments on PR #{pr_number}.")
        return CommandResult(text=f"PR #{pr_number} Comments\n{'=' * 40}\n{comments_json.strip()}")

    # ── /pr status ──
    if action == "status":
        rc, status_out, err = _run(
            ["gh", "pr", "status", "--json",
             "headRefName,state,title,number,mergeable,reviewDecision",
             "--jq", '.currentBranch // empty'],
            timeout=15,
        )
        # Simpler: just use gh pr status directly
        rc, status_out, err = _run(["gh", "pr", "status"], timeout=15)
        if rc != 0:
            return CommandResult(text=f"Failed to get PR status (is `gh` installed?): {err}", success=False)
        return CommandResult(text=f"PR Status\n{'=' * 40}\n{status_out.strip()}")

    # ── /pr create (default) ──
    context = rest if action == "create" else args

    from src.agent.git_utils import get_default_branch
    default = get_default_branch()
    _, log_text, _ = _git("log", f"{default}..HEAD", "--oneline", "--no-decorate")
    _, diff_stat, _ = _git("diff", f"{default}..HEAD", "--stat")

    if not log_text.strip():
        return CommandResult(text=f"No commits ahead of {default}. Nothing to PR.", success=False)

    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for PR generation.", success=False)

    prompt = (
        f"Draft a GitHub pull request for branch '{branch}'.\n"
        f"Context: {context or 'none provided'}\n\n"
        f"Commits:\n{log_text[:2000]}\n\n"
        f"Diff stat:\n{diff_stat[:2000]}\n\n"
        "Return a PR with:\n- Title (one line, under 70 chars)\n"
        "- Body with: ## Summary (1-3 bullets), ## Test plan (checklist)"
    )
    try:
        result = await brain.agent_loop(prompt, max_steps=2)
        return CommandResult(text=f"PR Draft for {branch}\n{'=' * 40}\n{result}")
    except Exception as e:
        return CommandResult(text=f"PR generation failed: {e}", success=False)


# ── /issue ─────────────────────────────────────────────────────────────

@command("issue", description="Draft a GitHub issue",
         usage="/issue [title]", category="git", permission=PermLevel.STANDARD)
async def cmd_issue(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    title = ctx.args.strip()

    if not title:
        return CommandResult(text="Usage: /issue <title or description>", success=False)

    if brain and hasattr(brain, "agent_loop"):
        prompt = (
            f"Draft a GitHub issue with title: {title}\n"
            "Return structured markdown with: Title, Description, Steps to Reproduce (if bug), "
            "Expected Behavior, Labels suggestion."
        )
        try:
            result = await brain.agent_loop(prompt, max_steps=1)
            return CommandResult(text=f"Issue Draft\n{'=' * 40}\n{result}")
        except Exception as e:
            return CommandResult(text=f"Issue generation failed: {e}", success=False)

    return CommandResult(text=f"Issue: {title}\n\n(Agent unavailable — write body manually)")


# ── /branch ────────────────────────────────────────────────────────────

@command("branch", aliases=["br"], description="Git branch management: list, create, switch, delete, recent",
         usage="/branch [list|create|switch|delete|recent] <name>", category="git", permission=PermLevel.STANDARD)
async def cmd_branch(ctx: CommandContext) -> CommandResult:
    parts = ctx.args.strip().split(None, 1)
    action = parts[0].lower() if parts else "list"
    name = parts[1] if len(parts) > 1 else ""

    if action == "list" or not parts:
        # Show current branch prominently, then all branches
        _, current, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
        current = current.strip()
        rc, out, err = _git("branch", "-a", "--no-color")
        lines = [f"Current branch: {current}", "-" * 40]
        if out.strip():
            lines.append(out.strip())
        else:
            lines.append("No branches found.")
        return CommandResult(text="\n".join(lines))

    elif action == "recent":
        # Show recently checked-out branches via reflog
        rc, out, err = _git("reflog", "--format=%gs", "-n", "50")
        if rc != 0 or not out.strip():
            return CommandResult(text="No recent branch history found.")
        seen = []
        for line in out.strip().splitlines():
            if "checkout: moving from" in line:
                # Extract the target branch
                parts2 = line.split(" to ")
                if len(parts2) >= 2:
                    br = parts2[-1].strip()
                    if br not in seen:
                        seen.append(br)
                if len(seen) >= 10:
                    break
        if not seen:
            return CommandResult(text="No recent branch switches found.")
        lines = ["Recent Branches", "-" * 30]
        for i, br in enumerate(seen, 1):
            lines.append(f"  {i}. {br}")
        return CommandResult(text="\n".join(lines))

    elif action == "create":
        if not name:
            return CommandResult(text="Usage: /branch create <name>", success=False)
        rc, out, err = _git("checkout", "-b", name)
        if rc != 0:
            return CommandResult(text=f"Failed: {err}", success=False)
        return CommandResult(text=f"Created and switched to branch: {name}")

    elif action == "switch":
        if not name:
            return CommandResult(text="Usage: /branch switch <name>", success=False)
        rc, out, err = _git("checkout", name)
        if rc != 0:
            return CommandResult(text=f"Failed: {err}", success=False)
        return CommandResult(text=f"Switched to branch: {name}")

    elif action == "delete":
        if not name:
            return CommandResult(text="Usage: /branch delete <name>", success=False)
        rc, out, err = _git("branch", "-d", name)
        if rc != 0:
            return CommandResult(text=f"Failed (use git branch -D for force): {err}", success=False)
        return CommandResult(text=f"Deleted branch: {name}")

    else:
        # Treat as branch name -- create if it doesn't exist, switch if it does
        rc, _, _ = _git("rev-parse", "--verify", f"refs/heads/{action}")
        if rc == 0:
            rc, out, err = _git("checkout", action)
            if rc != 0:
                return CommandResult(text=f"Failed to switch: {err}", success=False)
            return CommandResult(text=f"Switched to branch: {action}")
        else:
            rc, out, err = _git("checkout", "-b", action)
            if rc != 0:
                return CommandResult(text=f"Failed to create branch: {err}", success=False)
            return CommandResult(text=f"Created and switched to new branch: {action}")


# ── /worktree ──────────────────────────────────────────────────────────

@command("worktree", aliases=["wt"], description="Git worktree management",
         usage="/worktree [list|add|remove] <args>", category="git", permission=PermLevel.STANDARD)
async def cmd_worktree(ctx: CommandContext) -> CommandResult:
    parts = ctx.args.strip().split(None, 1)
    action = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""

    if action == "list" or not parts:
        rc, out, _ = _git("worktree", "list")
        return CommandResult(text=out.strip() if out.strip() else "No worktrees.")

    elif action == "add":
        if not rest:
            return CommandResult(text="Usage: /worktree add <path> [branch]", success=False)
        args = rest.split()
        rc, out, err = _git("worktree", "add", *args)
        if rc != 0:
            return CommandResult(text=f"Failed: {err}", success=False)
        return CommandResult(text=f"Worktree added: {args[0]}")

    elif action == "remove":
        if not rest:
            return CommandResult(text="Usage: /worktree remove <path>", success=False)
        rc, out, err = _git("worktree", "remove", rest)
        if rc != 0:
            return CommandResult(text=f"Failed: {err}", success=False)
        return CommandResult(text=f"Worktree removed: {rest}")

    else:
        return CommandResult(text=f"Unknown worktree action: {action}", success=False)


# ── /diff ──────────────────────────────────────────────────────────────

@command("diff", aliases=["d"], description="View uncommitted changes and per-turn diffs with color annotations",
         usage="/diff [--staged] [--branch <name>] [path]", category="git", permission=PermLevel.READ_ONLY)
async def cmd_diff(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    staged_only = "--staged" in args
    args = args.replace("--staged", "").strip()

    # Parse --branch flag
    branch_target = ""
    if "--branch" in args:
        parts = args.split("--branch", 1)
        args = parts[0].strip()
        branch_rest = parts[1].strip().split(None, 1)
        branch_target = branch_rest[0] if branch_rest else ""
        if len(branch_rest) > 1:
            args = branch_rest[1]

    path_filter = args.strip() or None

    def _colorize_diff(diff_text: str) -> str:
        """Add text markers for diff lines to aid readability."""
        colored = []
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                colored.append(f"  {line}")
            elif line.startswith("+"):
                colored.append(f"+ {line[1:]}")
            elif line.startswith("-"):
                colored.append(f"- {line[1:]}")
            elif line.startswith("@@"):
                colored.append(f"  {line}")
            else:
                colored.append(f"  {line}")
        return "\n".join(colored)

    lines = []

    # Branch comparison mode
    if branch_target:
        from src.agent.git_utils import get_diff_from_branch
        diff = get_diff_from_branch(base=branch_target)
        if not diff:
            return CommandResult(text=f"No diff against branch '{branch_target}'.")
        _, current, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
        lines.append(f"Diff: {current.strip()} vs {branch_target}")
        lines.append("=" * 50)
        lines.append(_colorize_diff(diff[:12000]))
        return CommandResult(text="\n".join(lines))

    # Normal staged/unstaged mode
    if staged_only:
        _, staged, _ = _git("diff", "--cached", "--no-color", *(["--", path_filter] if path_filter else []))
        if not staged.strip():
            return CommandResult(text="No staged changes.")
        lines.append("Staged Changes\n" + "=" * 40)
        lines.append(_colorize_diff(staged.strip()))
    else:
        _, staged, _ = _git("diff", "--cached", "--no-color", *(["--", path_filter] if path_filter else []))
        _, unstaged, _ = _git("diff", "--no-color", *(["--", path_filter] if path_filter else []))

        if staged.strip():
            lines.append("Staged Changes\n" + "=" * 40)
            lines.append(_colorize_diff(staged.strip()))
        if unstaged.strip():
            if lines:
                lines.append("")
            lines.append("Unstaged Changes\n" + "=" * 40)
            lines.append(_colorize_diff(unstaged.strip()))

    if not lines:
        return CommandResult(text="No changes detected.")
    return CommandResult(text="\n".join(lines))


# ── /review ────────────────────────────────────────────────────────────

@command("review", aliases=["rev"], description="AI code review (diff or file), with optional --security flag",
         usage="/review [--security] [path]", category="git", permission=PermLevel.STANDARD)
async def cmd_review(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for code review.", success=False)

    raw_args = ctx.args.strip()
    security_mode = "--security" in raw_args
    target = raw_args.replace("--security", "").strip()

    if target:
        path = Path(target).expanduser()
        if path.is_file():
            content = path.read_text(errors="replace")[:8000]
            if security_mode:
                prompt = (
                    f"Review this code file ({path.name}) for security vulnerabilities ONLY.\n\n"
                    "Focus on:\n"
                    "- Input validation gaps (SQL injection, XSS, command injection, path traversal)\n"
                    "- Authentication/authorization issues\n"
                    "- Cryptographic weaknesses (hardcoded secrets, weak algorithms)\n"
                    "- Data exposure (PII leaks, debug info in production)\n"
                    "- Dependency vulnerabilities\n\n"
                    "Rules:\n"
                    "- Rate each finding: CRITICAL / HIGH / MEDIUM / LOW\n"
                    "- Include file path and line number for each finding\n"
                    "- Suggest specific fixes\n\n"
                    f"```\n{content}\n```"
                )
            else:
                prompt = (
                    f"Review this code file ({path.name}) for bugs, security issues, "
                    f"and improvements:\n\n```\n{content}\n```"
                )
        else:
            return CommandResult(text=f"File not found: {path}", success=False)
    else:
        # Get diff -- prefer branch diff for security reviews
        if security_mode:
            from src.agent.git_utils import (
                get_unstaged_diff, get_staged_diff,
                get_branch_name, get_default_branch, get_diff_from_branch,
            )
            branch = get_branch_name()
            default = get_default_branch()
            if branch and branch != default:
                diff = get_diff_from_branch()
                scope = f"branch '{branch}' vs '{default}'"
            else:
                diff = get_staged_diff() or get_unstaged_diff()
                scope = "pending changes"
        else:
            _, diff, _ = _git("diff", "--no-color")
            if not diff.strip():
                _, diff, _ = _git("diff", "--cached", "--no-color")
            scope = "working tree"

        if not diff or not diff.strip():
            return CommandResult(text="No changes to review.", success=False)

        if security_mode:
            prompt = (
                f"Review these code changes for security vulnerabilities ONLY.\n\n"
                "Focus on:\n"
                "- Input validation gaps (SQL injection, XSS, command injection, path traversal)\n"
                "- Authentication/authorization issues\n"
                "- Cryptographic weaknesses (hardcoded secrets, weak algorithms)\n"
                "- Data exposure (PII leaks, debug info in production)\n"
                "- Dependency vulnerabilities\n\n"
                "Rules:\n"
                "- ONLY report NEW vulnerabilities introduced by these changes\n"
                "- Do NOT report pre-existing issues in unchanged code\n"
                "- Rate each finding: CRITICAL / HIGH / MEDIUM / LOW\n"
                "- Include file path and line number for each finding\n"
                "- Suggest specific fixes\n\n"
                f"Changes to review ({scope}):\n\n"
                f"```diff\n{diff[:8000]}\n```"
            )
        else:
            prompt = (
                "Review this git diff for bugs, security issues, and improvements:\n\n"
                f"```diff\n{diff[:8000]}\n```"
            )

    review_type = "Security Review" if security_mode else "Code Review"
    try:
        result = await brain.agent_loop(prompt, max_steps=3)
        return CommandResult(text=f"{review_type}\n{'=' * 40}\n{result}")
    except Exception as e:
        return CommandResult(text=f"Review failed: {e}", success=False)


# ── /bughunter ─────────────────────────────────────────────────────────

@command("bughunter", aliases=["bugs"], description="Scan for bugs using AI analysis",
         usage="/bughunter [scope]", category="git", permission=PermLevel.STANDARD)
async def cmd_bughunter(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for bug hunting.", success=False)

    scope = ctx.args.strip() or "."
    _, diff, _ = _git("diff", "--no-color")
    _, ls_files, _ = _git("ls-files", "--modified")

    prompt = (
        f"Scan the following for bugs, race conditions, security vulnerabilities, "
        f"and common mistakes. Scope: {scope}\n\n"
        f"Modified files:\n{ls_files[:2000]}\n\n"
        f"Recent diff:\n{diff[:4000]}\n\n"
        "List each issue with severity (critical/warning/info), location, and fix."
    )
    try:
        result = await brain.agent_loop(prompt, max_steps=5)
        return CommandResult(text=f"Bug Hunter Report\n{'=' * 40}\n{result}")
    except Exception as e:
        return CommandResult(text=f"Bug hunt failed: {e}", success=False)


# ── /explain ───────────────────────────────────────────────────────────

@command("explain", aliases=["ex"], description="Explain code (AI reads and explains)",
         usage="/explain <path_or_code>", category="git", permission=PermLevel.READ_ONLY)
async def cmd_explain(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for code explanation.", success=False)

    target = ctx.args.strip()
    if not target:
        return CommandResult(text="Usage: /explain <path or code snippet>", success=False)

    path = Path(target).expanduser()
    if path.is_file():
        content = path.read_text(errors="replace")[:8000]
        prompt = (
            f"Explain this code file ({path.name}) clearly and concisely. "
            f"Cover purpose, key logic, and notable patterns:\n\n```\n{content}\n```"
        )
    else:
        # Treat as inline code or symbol
        prompt = (
            f"Explain this code clearly and concisely:\n\n```\n{target}\n```"
        )

    try:
        result = await brain.agent_loop(prompt, max_steps=2)
        return CommandResult(text=f"Explanation\n{'=' * 40}\n{result}")
    except Exception as e:
        return CommandResult(text=f"Explain failed: {e}", success=False)


# ── /commit-push-pr ───────────────────────────────────────────────────

@command("commit-push-pr", aliases=["cpp", "ship"],
         description="Commit, push, and create PR in one step",
         usage="/commit-push-pr [message]", category="git")
async def cmd_commit_push_pr(ctx: CommandContext) -> CommandResult:
    """Commit staged changes, push to remote, and create a PR."""
    import shutil

    message = ctx.args.strip() if ctx.args else ""

    # Step 1: Check for changes
    try:
        rc, status_out, _ = _git("status", "--porcelain")
        if not status_out.strip():
            return CommandResult(text="Nothing to commit. Stage some changes first.")
    except Exception as e:
        return CommandResult(text=f"Git error: {e}", success=False)

    # Step 2: Stage all if nothing staged
    rc, staged, _ = _git("diff", "--cached", "--stat")
    if not staged.strip():
        _git("add", "-A")

    # Step 3: Generate commit message if not provided
    if not message:
        try:
            _, diff_text, _ = _git("diff", "--cached", "--stat")
            if ctx.brain and hasattr(ctx.brain, "think"):
                message = await ctx.brain.think(
                    f"Generate a concise git commit message (imperative mood, max 72 chars) for:\n{diff_text[:2000]}"
                )
                message = message.strip().strip('"').strip("'").split("\n")[0][:72]
            else:
                message = "Update files"
        except Exception:
            message = "Update files"

    steps = []

    # Step 4: Commit
    try:
        # Add Co-Authored-By
        try:
            from src.agent.commit_attribution import get_attribution_tracker
            trailer = get_attribution_tracker().get_co_author_trailer()
            full_message = f"{message}\n\n{trailer}"
        except Exception:
            full_message = message

        rc, out, err = _git("commit", "-m", full_message)
        if rc == 0:
            steps.append(f"Committed: {message}")
        else:
            return CommandResult(text=f"Commit failed: {err}", success=False)
    except Exception as e:
        return CommandResult(text=f"Commit error: {e}", success=False)

    # Step 5: Push
    try:
        _, branch_out, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
        branch_name = branch_out.strip()
        rc, out, err = _git("push", "-u", "origin", branch_name, timeout=60)
        if rc == 0:
            steps.append(f"Pushed to origin/{branch_name}")
        else:
            steps.append(f"Push failed: {err.strip()}")
            return CommandResult(text="\n".join(steps), success=False)
    except Exception as e:
        steps.append(f"Push error: {e}")
        return CommandResult(text="\n".join(steps), success=False)

    # Step 6: Create PR
    try:
        if shutil.which("gh"):
            rc, pr_out, pr_err = _run(
                ["gh", "pr", "create", "--fill", "--head", branch_name],
                timeout=30,
            )
            if rc == 0:
                pr_url = pr_out.strip()
                steps.append(f"PR created: {pr_url}")
            elif "already exists" in pr_err:
                steps.append(f"PR already exists for {branch_name}")
            else:
                steps.append(f"PR creation failed: {pr_err.strip()}")
        else:
            steps.append("GitHub CLI (gh) not found -- skipped PR creation")
    except Exception as e:
        steps.append(f"PR error: {e}")

    return CommandResult(text="\n".join(steps))


# ── /index ─────────────────────────────────────────────────────────────

@command("index", description="Manage codebase index: fast project context for JARVIS",
         usage="/index [build|rebuild|status|clear]", category="git", permission=PermLevel.STANDARD)
async def cmd_index(ctx: CommandContext) -> CommandResult:
    """Build and manage the two-tier codebase index.

    /index build   — Walk tree, extract symbols, populate cache
    /index rebuild — Force rebuild (ignores existing cache)
    /index status  — Show index stats and token estimate
    /index clear   — Delete the index cache
    """
    from src.indexer.builder import build_index, get_status, _summaries_path
    import time

    args = ctx.args.strip().lower() if ctx.args else "build"
    action = args.split()[0] if args else "build"
    root = Path.cwd()

    jarvis_dir = root / ".jarvis"
    if not jarvis_dir.exists() and action != "status":
        return CommandResult(
            text="No .jarvis/ found. Run /init first to initialize this project.",
            success=False,
        )

    # ── status ──
    if action == "status":
        s = get_status(root)
        if not s["initialized"]:
            return CommandResult(text="Project not initialized. Run /init first.")
        if not s["index_exists"]:
            return CommandResult(
                text="No index built yet. Run /index build to create one."
            )
        lines = [
            "Codebase Index Status",
            "=" * 40,
            f"  Files indexed:     {s['entries']}",
            f"  Files w/ symbols:  {s['files_with_symbols']}",
            f"  Cache size:        {s['cache_size_bytes'] // 1024}KB",
            f"  Est. tokens:       ~{s['estimated_tokens']}",
        ]
        if s["updated_at"]:
            lines.append(f"  Last built:        {s['updated_at'][:19].replace('T', ' ')} UTC")
        return CommandResult(text="\n".join(lines))

    # ── clear ──
    if action == "clear":
        p = _summaries_path(root)
        if p.exists():
            p.unlink()
            return CommandResult(text="Codebase index cleared.")
        return CommandResult(text="No index to clear.")

    # ── build / rebuild ──
    force = action == "rebuild"
    verb = "Rebuilding" if force else "Building"

    lines = [f"{verb} codebase index for {root.name}..."]
    t0 = time.monotonic()

    stats = build_index(root=root, force=force)

    if "error" in stats:
        return CommandResult(text=stats["error"], success=False)

    elapsed = stats["duration_ms"]
    lines += [
        "=" * 40,
        f"  Files scanned:   {stats['files_scanned']} / {stats['total_found']} total",
        f"  Files updated:   {stats['files_updated']}",
        f"  Symbols found:   {stats['symbols_found']}",
        f"  Cache size:      {stats['cache_size_bytes'] // 1024}KB",
        f"  Duration:        {elapsed}ms",
    ]
    if stats.get("file_cap_hit"):
        lines += [
            "",
            f"⚠ File cap hit: {stats['files_scanned']} of {stats['total_found']} files indexed.",
            f"  To include more, increase MAX_FILES in src/indexer/builder.py (currently {stats['files_scanned']}).",
        ]
    lines += [
        "",
        "Index injected automatically at session start. Run /index status to see token estimate.",
    ]
    return CommandResult(text="\n".join(lines))
