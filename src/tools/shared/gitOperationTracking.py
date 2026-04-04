"""
Shell-agnostic git operation tracking for usage metrics.

Detects `git commit`, `git push`, `gh pr create`, `glab mr create`, and
curl-based PR creation in command strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional


CommitKind = Literal["committed", "amended", "cherry-picked"]
BranchAction = Literal["merged", "rebased"]
PrAction = Literal["created", "edited", "merged", "commented", "closed", "ready"]


def _git_cmd_re(subcmd: str, suffix: str = "") -> re.Pattern[str]:
    """Build a regex that matches `git <subcmd>` while tolerating global options."""
    return re.compile(
        rf"\bgit(?:\s+-[cC]\s+\S+|\s+--\S+=\S+)*\s+{subcmd}\b{suffix}"
    )


_GIT_COMMIT_RE = _git_cmd_re("commit")
_GIT_PUSH_RE = _git_cmd_re("push")
_GIT_CHERRY_PICK_RE = _git_cmd_re("cherry-pick")
_GIT_MERGE_RE = _git_cmd_re("merge", r"(?!-)")
_GIT_REBASE_RE = _git_cmd_re("rebase")

_GH_PR_ACTIONS: list[tuple[re.Pattern[str], PrAction, str]] = [
    (re.compile(r"\bgh\s+pr\s+create\b"), "created", "pr_create"),
    (re.compile(r"\bgh\s+pr\s+edit\b"), "edited", "pr_edit"),
    (re.compile(r"\bgh\s+pr\s+merge\b"), "merged", "pr_merge"),
    (re.compile(r"\bgh\s+pr\s+comment\b"), "commented", "pr_comment"),
    (re.compile(r"\bgh\s+pr\s+close\b"), "closed", "pr_close"),
    (re.compile(r"\bgh\s+pr\s+ready\b"), "ready", "pr_ready"),
]


@dataclass
class PrInfo:
    pr_number: int
    pr_url: str
    pr_repository: str


@dataclass
class GitOperationResult:
    commit: Optional[dict] = None
    push: Optional[dict] = None
    branch: Optional[dict] = None
    pr: Optional[dict] = None


def parse_git_commit_id(stdout: str) -> Optional[str]:
    """Parse commit SHA from git commit output."""
    match = re.search(r"\[[\w./-]+(?: \(root-commit\))? ([0-9a-f]+)\]", stdout)
    return match.group(1) if match else None


def _parse_pr_url(url: str) -> Optional[PrInfo]:
    """Parse PR info from a GitHub PR URL."""
    match = re.search(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
    if match:
        return PrInfo(
            pr_number=int(match.group(2)),
            pr_url=url,
            pr_repository=match.group(1),
        )
    return None


def _find_pr_in_stdout(stdout: str) -> Optional[PrInfo]:
    """Find a GitHub PR URL embedded anywhere in stdout and parse it."""
    m = re.search(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+", stdout)
    return _parse_pr_url(m.group(0)) if m else None


def _parse_git_push_branch(output: str) -> Optional[str]:
    """Parse branch name from git push output."""
    match = re.search(
        r"^\s*[+\-*!= ]?\s*(?:\[new branch\]|\S+\.\.+\S+)\s+\S+\s*->\s*(\S+)",
        output,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def _parse_pr_number_from_text(stdout: str) -> Optional[int]:
    """Extract PR number from gh pr merge/close/ready output."""
    match = re.search(r"[Pp]ull request (?:\S+#)?#?(\d+)", stdout)
    return int(match.group(1)) if match else None


def _parse_ref_from_command(command: str, verb: str) -> Optional[str]:
    """Extract target ref from `git merge <ref>` / `git rebase <ref>` command."""
    parts = _git_cmd_re(verb).split(command, maxsplit=1)
    if len(parts) < 2:
        return None
    after = parts[1].strip()
    for t in after.split():
        if re.match(r"^[&|;><]", t):
            break
        if t.startswith("-"):
            continue
        return t
    return None


def detect_git_operation(command: str, output: str) -> GitOperationResult:
    """Scan bash command + output for git operations worth surfacing."""
    result = GitOperationResult()

    is_cherry_pick = bool(_GIT_CHERRY_PICK_RE.search(command))
    if _GIT_COMMIT_RE.search(command) or is_cherry_pick:
        sha = parse_git_commit_id(output)
        if sha:
            kind: CommitKind
            if is_cherry_pick:
                kind = "cherry-picked"
            elif "--amend" in command:
                kind = "amended"
            else:
                kind = "committed"
            result.commit = {"sha": sha[:6], "kind": kind}

    if _GIT_PUSH_RE.search(command):
        branch = _parse_git_push_branch(output)
        if branch:
            result.push = {"branch": branch}

    if _GIT_MERGE_RE.search(command) and re.search(
        r"(Fast-forward|Merge made by)", output
    ):
        ref = _parse_ref_from_command(command, "merge")
        if ref:
            result.branch = {"ref": ref, "action": "merged"}

    if _GIT_REBASE_RE.search(command) and "Successfully rebased" in output:
        ref = _parse_ref_from_command(command, "rebase")
        if ref:
            result.branch = {"ref": ref, "action": "rebased"}

    pr_action: Optional[PrAction] = None
    for pat, action, _op in _GH_PR_ACTIONS:
        if pat.search(command):
            pr_action = action
            break

    if pr_action:
        pr = _find_pr_in_stdout(output)
        if pr:
            result.pr = {"number": pr.pr_number, "url": pr.pr_url, "action": pr_action}
        else:
            num = _parse_pr_number_from_text(output)
            if num:
                result.pr = {"number": num, "action": pr_action}

    return result


def track_git_operations(command: str, exit_code: int, stdout: str = "") -> None:
    """Track git operations for analytics (stub -- add analytics as needed)."""
    if exit_code != 0:
        return
    # Analytics tracking would go here
