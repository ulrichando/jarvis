"""Git utility functions for JARVIS agent operations.

Ported from Claude Code's git utilities. Provides repository info,
diff generation, worktree management, commit attribution, and safety
checks. All functions handle errors gracefully -- returning None or
empty strings on failure, never raising.

Python 3.10+, stdlib only.
"""

from __future__ import annotations

import difflib
import functools
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GitInfo:
    """Snapshot of a git repository's state."""
    root: str
    branch: str
    default_branch: str
    is_worktree: bool
    is_dirty: bool
    ahead: int = 0
    behind: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_git(
    *args: str,
    cwd: str = "",
    timeout: int = 10,
    max_chars: int = 0,
) -> str | None:
    """Run a git command and return stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or None,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        if max_chars and len(out) > max_chars:
            out = out[:max_chars] + f"\n... (truncated at {max_chars} chars)"
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Cached functions
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=50)
def find_git_root(path: str = ".") -> str | None:
    """Walk up directories looking for .git (dir or file for worktrees/submodules).

    Returns the absolute path of the directory containing .git, or None.
    """
    try:
        current = Path(path).resolve()
    except (OSError, ValueError):
        return None

    while True:
        git_path = current / ".git"
        try:
            if git_path.exists():
                return str(current)
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


@functools.lru_cache(maxsize=50)
def get_default_branch(root: str = "") -> str:
    """Determine the default branch name for the repository.

    Checks refs/remotes/origin/HEAD first, then falls back to checking
    whether 'main' or 'master' exist locally.
    """
    effective_root = root or "."

    # Try origin/HEAD
    head_ref = _run_git(
        "symbolic-ref", "refs/remotes/origin/HEAD", cwd=effective_root
    )
    if head_ref:
        # refs/remotes/origin/main -> main
        parts = head_ref.rsplit("/", 1)
        if len(parts) == 2:
            return parts[1]

    # Check if 'main' branch exists
    result = _run_git("rev-parse", "--verify", "refs/heads/main", cwd=effective_root)
    if result is not None:
        return "main"

    # Check if 'master' branch exists
    result = _run_git("rev-parse", "--verify", "refs/heads/master", cwd=effective_root)
    if result is not None:
        return "master"

    return "main"


# ---------------------------------------------------------------------------
# Git info
# ---------------------------------------------------------------------------

def get_branch_name(root: str = "") -> str:
    """Return the current branch name, or 'HEAD' if detached."""
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=root or ".")
    return result if result else "HEAD"


def is_inside_git_repo(path: str = ".") -> bool:
    """Return True if *path* is inside a git working tree."""
    result = _run_git(
        "rev-parse", "--is-inside-work-tree", cwd=path
    )
    return result == "true"


def get_git_status(root: str = "", max_chars: int = 2000) -> str:
    """Return `git status --short`, truncated to *max_chars*."""
    result = _run_git("status", "--short", cwd=root or ".", max_chars=max_chars)
    return result if result is not None else ""


def get_recent_commits(root: str = "", count: int = 5) -> list[dict]:
    """Return the last *count* commits as a list of dicts.

    Each dict has keys: hash, message, author, date.
    """
    fmt = "%H%x00%s%x00%an%x00%aI"
    result = _run_git(
        "log", f"-{count}", f"--format={fmt}", cwd=root or "."
    )
    if not result:
        return []

    commits: list[dict] = []
    for line in result.splitlines():
        parts = line.split("\x00")
        if len(parts) >= 4:
            commits.append({
                "hash": parts[0],
                "message": parts[1],
                "author": parts[2],
                "date": parts[3],
            })
    return commits


def get_git_info(path: str = ".") -> GitInfo | None:
    """Populate and return a full GitInfo snapshot, or None outside a repo."""
    root = find_git_root(path)
    if root is None:
        return None

    branch = get_branch_name(root)
    default = get_default_branch(root)
    dirty = bool(get_git_status(root))
    wt = is_worktree(root)

    # Ahead / behind tracking branch
    ahead = 0
    behind = 0
    ab = _run_git(
        "rev-list", "--left-right", "--count", f"HEAD...@{{upstream}}",
        cwd=root,
    )
    if ab:
        parts = ab.split()
        if len(parts) == 2:
            try:
                ahead = int(parts[0])
                behind = int(parts[1])
            except ValueError:
                pass

    return GitInfo(
        root=root,
        branch=branch,
        default_branch=default,
        is_worktree=wt,
        is_dirty=dirty,
        ahead=ahead,
        behind=behind,
    )


# ---------------------------------------------------------------------------
# Diff utilities
# ---------------------------------------------------------------------------

def generate_diff(
    path: str,
    old_content: str,
    new_content: str,
    context_lines: int = 3,
) -> str:
    """Generate a unified diff between two strings using difflib."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context_lines,
    )
    return "".join(diff)


def get_staged_diff(root: str = "") -> str:
    """Return `git diff --cached`."""
    result = _run_git("diff", "--cached", cwd=root or ".")
    return result if result is not None else ""


def get_unstaged_diff(root: str = "") -> str:
    """Return `git diff` (unstaged changes)."""
    result = _run_git("diff", cwd=root or ".")
    return result if result is not None else ""


def get_diff_from_branch(root: str = "", base: str = "") -> str:
    """Return `git diff {base}...HEAD`.

    If *base* is empty, uses the default branch.
    """
    effective_root = root or "."
    if not base:
        base = get_default_branch(effective_root)
    result = _run_git("diff", f"{base}...HEAD", cwd=effective_root)
    return result if result is not None else ""


# ---------------------------------------------------------------------------
# Worktree management
# ---------------------------------------------------------------------------

def create_worktree(branch_name: str, root: str = "") -> str:
    """Create a git worktree for *branch_name* and return its path.

    The worktree is placed in ``<root>/../<repo>-worktrees/<branch_name>``.
    Returns an empty string on failure.
    """
    effective_root = root or find_git_root(".") or "."
    repo_name = os.path.basename(os.path.abspath(effective_root))
    worktree_dir = os.path.join(
        os.path.dirname(os.path.abspath(effective_root)),
        f"{repo_name}-worktrees",
        branch_name.replace("/", "-"),
    )

    # Create with -b (new branch) -- if the branch exists, try without -b
    result = _run_git(
        "worktree", "add", "-b", branch_name, worktree_dir,
        cwd=effective_root,
    )
    if result is None:
        # Branch may already exist -- try checking it out
        result = _run_git(
            "worktree", "add", worktree_dir, branch_name,
            cwd=effective_root,
        )
    if result is None:
        return ""
    return worktree_dir


def remove_worktree(worktree_path: str) -> bool:
    """Remove a git worktree. Returns True on success."""
    # Determine the main repo root from the worktree
    root = find_git_root(worktree_path)
    if root is None:
        return False
    result = _run_git("worktree", "remove", worktree_path, "--force", cwd=root)
    return result is not None


def list_worktrees(root: str = "") -> list[dict]:
    """List worktrees as a list of dicts with keys: path, branch, head."""
    result = _run_git("worktree", "list", "--porcelain", cwd=root or ".")
    if not result:
        return []

    worktrees: list[dict] = []
    current: dict = {}
    for line in result.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):], "branch": "", "head": ""}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            # refs/heads/main -> main
            current["branch"] = ref.split("refs/heads/", 1)[-1]
        elif line == "" and current:
            worktrees.append(current)
            current = {}
    if current:
        worktrees.append(current)
    return worktrees


def is_worktree(path: str) -> bool:
    """Check if *path* is a git worktree (i.e. .git is a file, not a directory)."""
    git_path = os.path.join(path, ".git")
    try:
        return os.path.isfile(git_path)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Commit attribution
# ---------------------------------------------------------------------------

def get_file_last_modified(path: str) -> dict | None:
    """Return the last commit that modified *path*.

    Returns a dict with keys: hash, author, date, message -- or None.
    """
    abs_path = os.path.abspath(path)
    root = find_git_root(os.path.dirname(abs_path))
    if root is None:
        return None

    fmt = "%H%x00%an%x00%aI%x00%s"
    result = _run_git(
        "log", "-1", f"--format={fmt}", "--", abs_path, cwd=root
    )
    if not result:
        return None

    parts = result.split("\x00")
    if len(parts) < 4:
        return None
    return {
        "hash": parts[0],
        "author": parts[1],
        "date": parts[2],
        "message": parts[3],
    }


def get_blame_info(path: str, line: int) -> dict | None:
    """Return blame info for a specific line in *path*.

    Returns a dict with keys: hash, author, date, content -- or None.
    """
    abs_path = os.path.abspath(path)
    root = find_git_root(os.path.dirname(abs_path))
    if root is None:
        return None

    result = _run_git(
        "blame", "-L", f"{line},{line}", "--porcelain", abs_path, cwd=root
    )
    if not result:
        return None

    info: dict = {"hash": "", "author": "", "date": "", "content": ""}
    lines = result.splitlines()
    if lines:
        # First line: <hash> <orig_line> <final_line> [<num_lines>]
        first_parts = lines[0].split()
        if first_parts:
            info["hash"] = first_parts[0]

    for bl in lines:
        if bl.startswith("author "):
            info["author"] = bl[len("author "):]
        elif bl.startswith("author-time "):
            info["date"] = bl[len("author-time "):]
        elif bl.startswith("\t"):
            info["content"] = bl[1:]

    return info if info["hash"] else None


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

# Patterns that indicate destructive git commands
_DESTRUCTIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bgit\s+push\b.*--force\b"),
    re.compile(r"\bgit\s+push\b.*-f\b"),
    re.compile(r"\bgit\s+reset\b.*--hard\b"),
    re.compile(r"\bgit\s+clean\b.*-f\b"),
    re.compile(r"\bgit\s+checkout\b.*\s+--\s*$"),
    re.compile(r"\bgit\s+checkout\s+\.\s*$"),
    re.compile(r"\bgit\s+restore\s+\.\s*$"),
    re.compile(r"\bgit\s+branch\b.*-D\b"),
    re.compile(r"\bgit\s+branch\b.*--delete\s+--force\b"),
    re.compile(r"\bgit\s+rebase\b.*--force\b"),
    re.compile(r"\bgit\s+stash\s+drop\b"),
    re.compile(r"\bgit\s+stash\s+clear\b"),
]


def is_destructive_git_command(command: str) -> bool:
    """Detect potentially destructive git commands.

    Checks for patterns like push --force, reset --hard, clean -f,
    branch -D, checkout ., restore ., stash drop/clear.
    """
    return any(pat.search(command) for pat in _DESTRUCTIVE_PATTERNS)
