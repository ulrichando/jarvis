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


def publish_rollback(automod_id: str, rollback_sha: str, *, base: str = "master") -> Tuple[bool, str]:
    """After the watchdog auto-rolls-back an unhealthy deploy: record the
    rollback as an OPEN GitHub Issue for triage — the failure-path mirror of
    publish_deploy (which opens a CLOSED Issue for a shipped fix).

    On origin: a deploy is only pushed once it is health-CONFIRMED
    (publish_deploy), and a rollback happens BEFORE confirmation — so normally
    the bad commit never reached origin and there is nothing to revert. The one
    case that does diverge is a confirmed deploy that later regressed: if
    origin/<base> has moved past the last-good SHA, rewind it (the owner bypasses
    the no-force-push branch rule; force-with-lease so a concurrent push isn't
    clobbered). Best-effort + gated by the caller. Returns (True, issue_url) or
    (False, reason).
    """
    from pipeline.automod import artifact
    from pipeline.automod.summarize import summarize

    art = artifact.load(automod_id)

    # Refresh the tracking ref, then only rewind origin if it actually leads the
    # last-good SHA (a pushed-then-regressed deploy). Normal rollbacks: no-op.
    pushed_note = ""
    _git("fetch", "origin", base)
    ahead = _git("rev-list", "--count", f"{rollback_sha}..origin/{base}")
    try:
        n_ahead = int((ahead.stdout or "0").strip() or "0")
    except ValueError:
        n_ahead = 0
    if ahead.returncode == 0 and n_ahead > 0:
        push = _git("push", "--force-with-lease", "origin", f"{rollback_sha}:{base}")
        if push.returncode != 0:
            return False, f"rollback push failed: {push.stderr.strip()}"
        pushed_note = f" origin/`{base}` rewound to `{rollback_sha[:12]}`."

    s = summarize(art)
    body = (
        f"{s['markdown']}\n\n---\n_Auto-rolled-back by the JARVIS evolution "
        f"watchdog: unhealthy past its deploy window, reset to `{rollback_sha[:12]}` "
        f"locally.{pushed_note} Left OPEN for triage._"
    )
    issue = _gh("issue", "create", "--title", f"[evolution] ROLLED BACK: {s['title']}", "--body", body)
    if issue.returncode != 0:
        return False, f"gh issue create failed: {issue.stderr.strip() or issue.stdout.strip()}"
    issue_url = issue.stdout.strip()
    # Deliberately left OPEN — publish_deploy closes its Issue, but a rollback
    # needs a human to look at why the deploy regressed.

    try:
        artifact.update_status(
            automod_id, art.get("status", "auto-rolled-back"), issue_url=issue_url)
    except Exception:  # noqa: BLE001 - recording the URL is best-effort
        pass
    return True, issue_url
