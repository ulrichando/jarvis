"""GitHub connector subagent — read-only PR + issue lookup via `gh`.

SubagentSpec because all 4 tools share the delegate(role, task)
plumbing and don't need their own conversation thread. The supervisor
delegates with a goal string; this subagent's LLM picks among the 4
github_* tools internally.

v1 is read-only. Writes (comment, merge) defer to v2 with proper
destructive-verb gating.
"""
from __future__ import annotations

from .registry import SubagentSpec, register_subagent


GITHUB_INSTRUCTIONS = """\
You are JARVIS's GitHub subagent. The supervisor delegates a goal
string related to GitHub PRs or issues.

You have FOUR tools — pick the right one:

  github_list_prs(repo?, state?, limit?)        — "any open PRs"
  github_view_pr(num, repo?)                    — "show me PR 42"
  github_list_issues(repo?, state?, assignee?)  — "open issues",
                                                  "issues assigned to me"
  github_view_issue(num, repo?)                 — "show me issue 17"

Rules:
  - Pick ONE tool, call it once, report the result via task_done.
  - For "issues assigned to me" pass `assignee="@me"`.
  - If the user says "PR 42" without a repo, pass repo="" — the
    `gh` CLI auto-detects from the current directory's git remote.
  - If the user names a repo like "the jarvis repo" you can leave
    repo="" too — the agent runs from the jarvis repo by default.
  - For repos OTHER than current default (e.g. "anthropic/claude-code"),
    pass `repo="anthropic/claude-code"`.
  - Do NOT chain calls. One tool per delegate; if more is needed,
    return what you found and let the supervisor delegate again.
  - This subagent is READ-ONLY. If the user asks to comment, merge,
    or close, return "needs human action — voice-agent doesn't ship
    write tools yet, sir."
"""


def _github_tools() -> list:
    """Lazy import — keeps the gh subprocess setup out of registry-
    load critical path."""
    from tools.github import (
        github_list_prs, github_view_pr,
        github_list_issues, github_view_issue,
    )
    return [github_list_prs, github_view_pr, github_list_issues, github_view_issue]


_GITHUB_WHEN = (
    "Use for any GitHub PR or issue lookup: \"any open PRs\", "
    "\"list closed pull requests\", \"show me PR 42\", \"what issues "
    "are assigned to me\", \"summarize issue 17\". Read-only — "
    "doesn't comment, merge, or close (deferred to a future write-"
    "enabled version with destructive-verb confirmation). Auto-"
    "disabled if `gh` CLI is missing or unauthenticated."
)


def register_github() -> None:
    """Register the GitHub subagent. Self-disables when `gh` is
    missing or the user hasn't run `gh auth login`.

    DISABLED BY DEFAULT 2026-05-08 — opt in with `JARVIS_SUBAGENT_GITHUB=1`.
    Disabled alongside summarize/researcher etc. while supervisor delegate
    routing is being repaired.
    """
    import os
    try:
        from tools.github import is_available
        enabled = is_available()
    except Exception:
        enabled = False
    enabled = enabled and (
        os.environ.get("JARVIS_SUBAGENT_GITHUB", "0") == "1"
    )

    register_subagent(SubagentSpec(
        name="github",
        when_to_use=_GITHUB_WHEN,
        instructions=GITHUB_INSTRUCTIONS,
        tool_factory=_github_tools,
        ack_phrase="Looking it up.",
        max_history_items=4,
        enabled=enabled,
    ))
