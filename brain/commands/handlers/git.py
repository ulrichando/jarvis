"""Git & Code commands — version control, review, and code intelligence."""
import json
import subprocess
from pathlib import Path

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


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

@command("commit", aliases=["ci"], description="Generate commit message and commit",
         usage="/commit [message]", category="git", permission=PermLevel.STANDARD)
async def cmd_commit(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    msg = ctx.args.strip()

    # Check for staged changes first, fall back to unstaged
    rc, staged, _ = _git("diff", "--cached", "--stat")
    rc2, unstaged, _ = _git("diff", "--stat")

    if not staged.strip() and not unstaged.strip():
        return CommandResult(text="Nothing to commit — working tree clean.", success=False)

    # Auto-stage if nothing staged
    if not staged.strip():
        _git("add", "-A")

    if not msg:
        # Generate message via LLM
        _, diff_text, _ = _git("diff", "--cached")
        if brain and hasattr(brain, "agent_loop"):
            prompt = (
                "Generate a concise git commit message for these changes. "
                "Return ONLY the commit message, no explanation:\n\n" + diff_text[:4000]
            )
            try:
                result = await brain.agent_loop(prompt, max_steps=1)
                msg = result.strip().strip('"').strip("'")
            except Exception:
                msg = "Update files"
        else:
            msg = "Update files"

    rc, out, err = _git("commit", "-m", msg)
    if rc != 0:
        return CommandResult(text=f"Commit failed:\n{err}", success=False)
    return CommandResult(text=f"Committed: {msg}\n{out.strip()}")


# ── /pr ────────────────────────────────────────────────────────────────

@command("pr", description="Draft a pull request (AI-generated title & body)",
         usage="/pr [context]", category="git", permission=PermLevel.STANDARD)
async def cmd_pr(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    context = ctx.args.strip()

    _, branch, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    _, log_text, _ = _git("log", "main..HEAD", "--oneline", "--no-decorate")
    _, diff_stat, _ = _git("diff", "main..HEAD", "--stat")

    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for PR generation.", success=False)

    prompt = (
        f"Draft a GitHub pull request for branch '{branch}'.\n"
        f"Context: {context or 'none provided'}\n\n"
        f"Commits:\n{log_text[:2000]}\n\n"
        f"Diff stat:\n{diff_stat[:2000]}\n\n"
        "Return a PR with:\n- Title (one line)\n- Body (markdown summary, test plan)"
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

@command("branch", aliases=["br"], description="Git branch management",
         usage="/branch [list|create|switch] <name>", category="git", permission=PermLevel.STANDARD)
async def cmd_branch(ctx: CommandContext) -> CommandResult:
    parts = ctx.args.strip().split(None, 1)
    action = parts[0].lower() if parts else "list"
    name = parts[1] if len(parts) > 1 else ""

    if action == "list" or not parts:
        rc, out, err = _git("branch", "-a", "--no-color")
        return CommandResult(text=out.strip() if out.strip() else "No branches found.")

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

    else:
        # Treat as branch name to switch to
        rc, out, err = _git("checkout", action)
        if rc != 0:
            return CommandResult(text=f"Unknown action or branch: {action}\n{err}", success=False)
        return CommandResult(text=f"Switched to branch: {action}")


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

@command("diff", aliases=["d"], description="Show git diff (staged + unstaged)",
         usage="/diff", category="git", permission=PermLevel.READ_ONLY)
async def cmd_diff(ctx: CommandContext) -> CommandResult:
    _, staged, _ = _git("diff", "--cached", "--no-color")
    _, unstaged, _ = _git("diff", "--no-color")

    lines = []
    if staged.strip():
        lines.append("Staged Changes\n" + "─" * 40)
        lines.append(staged.strip())
    if unstaged.strip():
        if lines:
            lines.append("")
        lines.append("Unstaged Changes\n" + "─" * 40)
        lines.append(unstaged.strip())
    if not lines:
        return CommandResult(text="No changes detected.")
    return CommandResult(text="\n".join(lines))


# ── /review ────────────────────────────────────────────────────────────

@command("review", aliases=["rev"], description="AI code review (diff or file)",
         usage="/review [path]", category="git", permission=PermLevel.STANDARD)
async def cmd_review(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for code review.", success=False)

    target = ctx.args.strip()

    if target:
        path = Path(target).expanduser()
        if path.is_file():
            content = path.read_text(errors="replace")[:8000]
            prompt = (
                f"Review this code file ({path.name}) for bugs, security issues, "
                f"and improvements:\n\n```\n{content}\n```"
            )
        else:
            return CommandResult(text=f"File not found: {path}", success=False)
    else:
        _, diff, _ = _git("diff", "--no-color")
        if not diff.strip():
            _, diff, _ = _git("diff", "--cached", "--no-color")
        if not diff.strip():
            return CommandResult(text="No changes to review.", success=False)
        prompt = (
            "Review this git diff for bugs, security issues, and improvements:\n\n"
            f"```diff\n{diff[:8000]}\n```"
        )

    try:
        result = await brain.agent_loop(prompt, max_steps=3)
        return CommandResult(text=f"Code Review\n{'=' * 40}\n{result}")
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
