"""Publish a self-evolution proposal for review: push its branch + open a PR.

This is the "upload to branches, review from my phone" step. It pushes the
auto-mod proposal branch to origin and opens a GitHub PR whose body is the
plain-English summary, so Ulrich can review the diff + summary remotely and
approve. Approval (in the web /evolution page or by merging the PR) then drives
the deploy actuator + watchdog.

Outward-facing (pushes to origin + opens a PR), so callers gate it on intent.
"""
from __future__ import annotations

import subprocess
from typing import Tuple

from pipeline.automod.deploy import REPO_ROOT, _git


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args], cwd=str(REPO_ROOT),
        capture_output=True, text=True, check=False,
    )


def publish(automod_id: str, *, draft: bool = True, base: str = "master") -> Tuple[bool, str]:
    """Push the proposal branch + open (or find) a PR. Returns (True, pr_url) or
    (False, reason). Idempotent: if a PR already exists for the branch, returns
    its URL instead of failing."""
    from pipeline.automod import artifact
    from pipeline.automod.summarize import summarize

    art = artifact.load(automod_id)
    branch = art.get("branch")
    if not branch:
        return False, "artifact has no branch to publish"

    push = _git("push", "-u", "origin", branch)
    if push.returncode != 0:
        return False, f"push failed: {push.stderr.strip()}"

    s = summarize(art)
    create = [
        "pr", "create", "--head", branch, "--base", base,
        "--title", s["title"], "--body", s["markdown"],
    ]
    if draft:
        create.append("--draft")
    pr = _gh(*create)
    pr_url = pr.stdout.strip()

    if pr.returncode != 0:
        # Most common non-fatal cause: a PR already exists for this branch.
        existing = _gh("pr", "view", branch, "--json", "url", "-q", ".url")
        if existing.returncode == 0 and existing.stdout.strip():
            pr_url = existing.stdout.strip()
        else:
            return False, f"gh pr create failed: {pr.stderr.strip() or pr.stdout.strip()}"

    try:
        artifact.update_status(automod_id, art.get("status", "pending"), pr_url=pr_url)
    except Exception:  # noqa: BLE001 - recording the URL is best-effort
        pass
    return True, pr_url


def publish_deploy(automod_id: str, *, base: str = "master") -> Tuple[bool, str]:
    """After a deploy is health-confirmed by the watchdog: push the live commit to
    origin and record the shipped fix as a (closed) GitHub Issue.

    Distinct from publish() (which pushes a proposal BRANCH + opens a PR for
    review): this reflects an already-approved, already-live deploy on GitHub —
    so "deployed" means on origin/master + visible in the Issues history, not
    just on the local box. Best-effort + idempotent; the change is live locally
    regardless. Returns (True, issue_url) or (False, reason).
    """
    from pipeline.automod import artifact
    from pipeline.automod.summarize import summarize

    art = artifact.load(automod_id)

    push = _git("push", "origin", base)
    if push.returncode != 0:
        return False, f"push failed: {push.stderr.strip()}"

    s = summarize(art)
    sha = _git("rev-parse", "HEAD").stdout.strip()[:12]
    body = (
        f"{s['markdown']}\n\n---\n_Shipped by JARVIS self-evolution: approved, "
        f"deployed, and health-confirmed locally, then pushed to `{base}` as "
        f"`{sha}`. Closed because it is already live — the watchdog auto-reverts "
        f"it if it regresses._"
    )
    issue = _gh("issue", "create", "--title", f"[evolution] {s['title']}", "--body", body)
    if issue.returncode != 0:
        return False, f"gh issue create failed: {issue.stderr.strip() or issue.stdout.strip()}"
    issue_url = issue.stdout.strip()
    _gh("issue", "close", issue_url, "--reason", "completed")  # best-effort

    try:
        artifact.update_status(automod_id, art.get("status", "merged"), issue_url=issue_url)
    except Exception:  # noqa: BLE001 - recording the URL is best-effort
        pass
    return True, issue_url
