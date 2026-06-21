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
