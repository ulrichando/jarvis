"""GitHub connector — wraps the `gh` CLI as voice tools.

Why `gh` subprocess instead of PyGithub:
  - `gh` is already authed on the user's machine (~/.config/gh/hosts.yml)
  - No new Python deps; inherits whatever the user already configured
  - JSON output is stable across versions (--json flag locked in 2024)
  - Subprocess overhead (~50ms) is negligible vs the ~200-500ms HTTP
    round-trip to api.github.com that PyGithub also pays

Read-only for v1. Writes (pr comment, pr merge, issue comment) are
deferred — voice slip-of-the-tongue accidentally posting on a PR is
the failure mode we want to avoid; proper destructive-verb gating
needs design before shipping.

Tools exposed (4):
  - github_list_prs(repo?, state?, limit?)
  - github_view_pr(num, repo?)
  - github_list_issues(repo?, state?, assignee?, limit?)
  - github_view_issue(num, repo?)

`repo` defaults to the current directory's git remote when unset.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.github")


def _gh_path() -> str | None:
    """Resolve `gh` once. Cached at module level via lru-style check."""
    return shutil.which("gh")


async def _gh(args: list[str], cwd: str | None = None, timeout: float = 15.0) -> tuple[str, str, int]:
    """Run `gh <args>` and return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        "gh", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or os.getcwd(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return ("", f"gh timed out after {timeout}s", -1)
    return (out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), proc.returncode or 0)


def _format_short_user(login: str) -> str:
    """Voice-friendly user — drop the `@` if present."""
    return login.lstrip("@")


@function_tool
async def github_list_prs(repo: str = "", state: str = "open", limit: int = 5) -> str:
    """List GitHub pull requests. Use for "any open PRs" / "what
    pull requests are pending" / "list closed PRs from last week".

    Args:
        repo: 'owner/name' format. Empty = current git directory's
              default repo (per `gh repo set-default`).
        state: 'open' (default), 'closed', 'merged', or 'all'.
        limit: Max results (1-30, default 5).
    """
    if not _gh_path():
        return "(gh CLI not installed)"
    state = state.strip().lower() or "open"
    if state not in ("open", "closed", "merged", "all"):
        return f"(invalid state: {state}; use open/closed/merged/all)"
    limit = max(1, min(int(limit or 5), 30))
    args = [
        "pr", "list",
        "--state", state,
        "--limit", str(limit),
        "--json", "number,title,author,state,isDraft,headRefName,url",
    ]
    if repo:
        args.extend(["--repo", repo])
    out, err, rc = await _gh(args)
    if rc != 0:
        return f"(github error: {err.strip()[:200]})"
    try:
        prs = json.loads(out) if out.strip() else []
    except Exception:
        return f"(github returned invalid json)"
    if not prs:
        return f"No {state} pull requests."
    # Voice-format: "PR 42, 'fix the dispatcher', by ulrichando, draft"
    lines = []
    for p in prs[:limit]:
        author = _format_short_user(p.get("author", {}).get("login", "?"))
        title = (p.get("title") or "").strip()[:120]
        draft = " (draft)" if p.get("isDraft") else ""
        lines.append(f"PR {p['number']}: '{title}', by {author}{draft}")
    return f"{len(prs)} {state} pull request(s):\n  - " + "\n  - ".join(lines)


@function_tool
async def github_view_pr(num: int, repo: str = "") -> str:
    """Get details of a specific pull request — title, body, status,
    head/base branches, recent reviews. Use for "show me PR 42" /
    "what's the latest on pull request 17".

    Args:
        num: PR number.
        repo: 'owner/name' format. Empty = current dir default.
    """
    if not _gh_path():
        return "(gh CLI not installed)"
    if num is None or int(num) <= 0:
        return "(num required)"
    args = [
        "pr", "view", str(int(num)),
        "--json",
        "number,title,body,state,isDraft,author,headRefName,baseRefName,url,reviews,comments,mergeable",
    ]
    if repo:
        args.extend(["--repo", repo])
    out, err, rc = await _gh(args)
    if rc != 0:
        return f"(github error: {err.strip()[:200]})"
    try:
        pr = json.loads(out)
    except Exception:
        return "(github returned invalid json)"
    author = _format_short_user(pr.get("author", {}).get("login", "?"))
    title = (pr.get("title") or "").strip()
    body = (pr.get("body") or "").strip().replace("\n", " ")[:300]
    state = pr.get("state", "?").lower()
    draft = " (draft)" if pr.get("isDraft") else ""
    head = pr.get("headRefName", "?")
    base = pr.get("baseRefName", "?")
    n_reviews = len(pr.get("reviews") or [])
    n_comments = len(pr.get("comments") or [])
    parts = [
        f"PR {pr['number']}: '{title}' by {author} — {state}{draft}.",
        f"From {head} into {base}.",
        f"{n_reviews} review(s), {n_comments} comment(s).",
    ]
    if body:
        parts.append(f"Description: {body}")
    return " ".join(parts)


@function_tool
async def github_list_issues(
    repo: str = "",
    state: str = "open",
    assignee: str = "",
    limit: int = 5,
) -> str:
    """List GitHub issues. Use for "any open issues" / "list issues
    assigned to me" / "what issues are tagged bug".

    Args:
        repo: 'owner/name' format. Empty = current dir default.
        state: 'open' (default), 'closed', or 'all'.
        assignee: GitHub username, or '@me' for issues assigned to
                  the authenticated user. Empty = no assignee filter.
        limit: Max results (1-30, default 5).
    """
    if not _gh_path():
        return "(gh CLI not installed)"
    state = state.strip().lower() or "open"
    if state not in ("open", "closed", "all"):
        return f"(invalid state: {state}; use open/closed/all)"
    limit = max(1, min(int(limit or 5), 30))
    args = [
        "issue", "list",
        "--state", state,
        "--limit", str(limit),
        "--json", "number,title,author,state,labels,assignees,url",
    ]
    if repo:
        args.extend(["--repo", repo])
    if assignee:
        args.extend(["--assignee", assignee])
    out, err, rc = await _gh(args)
    if rc != 0:
        return f"(github error: {err.strip()[:200]})"
    try:
        issues = json.loads(out) if out.strip() else []
    except Exception:
        return "(github returned invalid json)"
    if not issues:
        scope = f" assigned to {assignee}" if assignee else ""
        return f"No {state} issues{scope}."
    lines = []
    for i in issues[:limit]:
        author = _format_short_user(i.get("author", {}).get("login", "?"))
        title = (i.get("title") or "").strip()[:120]
        labels = ", ".join(l.get("name") for l in (i.get("labels") or [])[:3])
        suffix = f" ({labels})" if labels else ""
        lines.append(f"Issue {i['number']}: '{title}', by {author}{suffix}")
    return f"{len(issues)} {state} issue(s):\n  - " + "\n  - ".join(lines)


@function_tool
async def github_view_issue(num: int, repo: str = "") -> str:
    """Get details of a specific issue — title, body, state, labels,
    assignees, comment count. Use for "show me issue 42" / "what's
    issue 17 about".

    Args:
        num: Issue number.
        repo: 'owner/name' format. Empty = current dir default.
    """
    if not _gh_path():
        return "(gh CLI not installed)"
    if num is None or int(num) <= 0:
        return "(num required)"
    args = [
        "issue", "view", str(int(num)),
        "--json",
        "number,title,body,state,author,labels,assignees,comments,url",
    ]
    if repo:
        args.extend(["--repo", repo])
    out, err, rc = await _gh(args)
    if rc != 0:
        return f"(github error: {err.strip()[:200]})"
    try:
        i = json.loads(out)
    except Exception:
        return "(github returned invalid json)"
    author = _format_short_user(i.get("author", {}).get("login", "?"))
    title = (i.get("title") or "").strip()
    body = (i.get("body") or "").strip().replace("\n", " ")[:300]
    state = i.get("state", "?").lower()
    labels = ", ".join(l.get("name") for l in (i.get("labels") or [])[:5])
    n_comments = len(i.get("comments") or [])
    parts = [
        f"Issue {i['number']}: '{title}' by {author} — {state}.",
        f"{n_comments} comment(s).",
    ]
    if labels:
        parts.append(f"Labels: {labels}.")
    if body:
        parts.append(f"Description: {body}")
    return " ".join(parts)


def is_available() -> bool:
    """True if `gh` CLI is on PATH AND the user appears to be authed.
    We check for a config file rather than running `gh auth status`
    (faster, no subprocess at registry-load time)."""
    if not _gh_path():
        return False
    cfg = Path.home() / ".config" / "gh" / "hosts.yml"
    return cfg.exists()
