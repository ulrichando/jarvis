"""Subcommands for bin/jarvis-automod (Spec B, Plane 3, D5).

list / show / merge / reject / revert. Used by the user to review
auto-mod proposals after the spawner has written them.

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

# When executed directly (e.g. via bin/jarvis-automod), ensure the
# voice-agent root is on sys.path so pipeline.* imports resolve.
_VA_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

from pipeline.automod import artifact, test_gate
from pipeline.automod._state import _automod_home

logger = logging.getLogger("jarvis.automod.cli")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=False, **kw)


# ---------------------------------------------------------------------------
# Public command functions
# ---------------------------------------------------------------------------

def cmd_list(only_pending: bool = True) -> list[dict]:
    """Return artifacts sorted by created_at desc.

    Globs automod-*.json from _automod_home(). When only_pending=True
    (the default), filters to status == "pending" only.
    """
    home = _automod_home()
    if not home.exists():
        return []
    rows: list[dict] = []
    for p in home.glob("automod-*.json"):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    if only_pending:
        rows = [r for r in rows if r.get("status") == "pending"]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


def cmd_show(automod_id: str) -> dict:
    """Return the full artifact dict for the given automod_id."""
    return artifact.load(automod_id)


def cmd_merge(automod_id: str) -> tuple[bool, str]:
    """git merge --ff-only the automod branch into master.

    Third defence layer: re-validates the diff at merge time (after
    spawner prompt + finalize check). Aborts on non-ff so there are
    never merge commits from auto-mod. On success: updates artifact
    status to "merged" and writes an audit record.

    Returns (True, merge_sha) or (False, reason_str).
    Refuses to merge if artifact status != "pending".
    """
    art = artifact.load(automod_id)
    if art.get("status") != "pending":
        return False, (
            f"artifact_status_{art.get('status', 'unknown')}_not_pending"
        )

    branch = art["branch"]

    # Re-validate the diff (3rd defence layer).
    diff = _git("diff", f"master..{branch}").stdout
    ok, reason = test_gate.validate_diff(diff)
    if not ok:
        return False, f"diff_validation_failed:{reason}"

    co = _git("checkout", "master")
    if co.returncode != 0:
        return False, f"checkout_failed:{co.stderr.strip()}"

    merge = _git("merge", "--ff-only", branch)
    if merge.returncode != 0:
        return False, f"ff_only_aborted: {merge.stderr.strip()}"

    merge_sha = _git("rev-parse", "HEAD").stdout.strip()
    artifact.update_status(
        automod_id,
        "merged",
        merged_at=_now_iso(),
        merge_sha=merge_sha,
    )
    artifact.audit("automod_merged", id=automod_id, merge_sha=merge_sha)
    return True, merge_sha


def cmd_reject(automod_id: str, reason: str) -> None:
    """Delete the automod branch and record the rejection reason.

    No-ops silently if the artifact is already non-pending (idempotent
    for safety — the user may call reject twice on a failed merge).
    """
    art = artifact.load(automod_id)
    if art.get("status") != "pending":
        return
    branch = art.get("branch")
    if branch:
        _git("checkout", "master")
        _git("branch", "-D", branch)
    artifact.update_status(
        automod_id,
        "rejected",
        rejected_at=_now_iso(),
        rejection_reason=reason,
    )
    artifact.audit("automod_rejected", id=automod_id, reason=reason)


def cmd_revert(commit_sha: str) -> tuple[bool, str]:
    """git revert --no-edit <sha>. Creates a new revert commit (never
    rewrites history). Returns (True, new_sha) or (False, error_msg).
    """
    rev = _git("revert", "--no-edit", commit_sha)
    if rev.returncode != 0:
        return False, rev.stderr.strip()
    new_sha = _git("rev-parse", "HEAD").stdout.strip()
    artifact.audit(
        "automod_reverted",
        reverted_sha=commit_sha,
        revert_sha=new_sha,
    )
    return True, new_sha


def revert(args) -> int:
    """Revert an auto-merged automod proposal OR a commit SHA.

    If ``args.target`` matches ``automod-YYYY-MM-DD-xxxxxx`` (starts with
    ``"automod-"`` and contains no ``"/"``), looks up the rollback ref from
    the artifact JSON, hard-resets master to the saved rollback SHA,
    force-with-lease pushes, and restarts the voice-agent service.

    Otherwise falls through to the legacy SHA-based ``cmd_revert`` path
    which creates an inverse revert commit via ``git revert --no-edit``.

    Returns 0 on success, 1 on operational error, 2 on usage/input error.
    """
    from pipeline.automod._state import artifact_path as _artifact_path

    target = getattr(args, "target", None)
    if not target:
        print("error: no target specified", file=sys.stderr)
        return 2

    # New path: automod ID lookup (e.g. "automod-2026-05-28-abc123").
    if target.startswith("automod-") and "/" not in target:
        artifact_file = _artifact_path(target)
        if not artifact_file.exists():
            print(f"error: artifact not found: {artifact_file}",
                  file=sys.stderr)
            return 2
        try:
            rec = json.loads(artifact_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"error: cannot read artifact {target}: {e}",
                  file=sys.stderr)
            return 2
        rollback_ref = rec.get("rollback_ref")
        rollback_sha = rec.get("rollback_sha")
        if not rollback_ref or not rollback_sha:
            print(
                f"error: artifact {target} has no rollback metadata "
                "(was it manually merged via the legacy path?)",
                file=sys.stderr,
            )
            return 2
        # Fetch the rollback ref from origin (in case it's only remote).
        # CalledProcessError means the ref doesn't exist on origin yet —
        # continue with the local commit-ish anyway.
        try:
            subprocess.check_call(
                ["git", "fetch", "origin",
                 f"{rollback_ref}:{rollback_ref}"],
            )
        except subprocess.CalledProcessError:
            pass
        subprocess.check_call(["git", "checkout", "master"])
        subprocess.check_call(["git", "reset", "--hard", rollback_sha])
        subprocess.check_call(
            ["git", "push", "--force-with-lease", "origin", "master:master"],
        )
        # Restart voice-agent so the reverted code is live.
        subprocess.run(
            ["systemctl", "--user", "restart",
             "jarvis-voice-agent.service"],
            check=False,
        )
        try:
            artifact.audit(
                "automod_reverted",
                id=target,
                rollback_ref=rollback_ref,
                rollback_sha=rollback_sha,
            )
        except Exception:  # noqa: BLE001
            pass
        print(
            f"reverted: master reset to {rollback_sha[:8]} "
            f"(rollback ref {rollback_ref})"
        )
        return 0

    # Legacy path: SHA-based revert (creates an inverse commit).
    ok, info = cmd_revert(target)
    if ok:
        print(f"Reverted. New SHA: {info}")
        return 0
    print(f"Revert failed: {info}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_list_rows(rows: list[dict]) -> None:
    if not rows:
        print("(no auto-mod artifacts)")
        return
    for r in rows:
        intent_short = (r.get("intent") or "")[:80]
        print(
            f"{r['id']:<35}  {r.get('kind', '?'):<10}  "
            f"{r.get('status', '?'):<10}  {intent_short}"
        )


_RESTART_GUIDANCE = """\
[NEXT] Restart the service to pick up the change:
   sqlite3 ~/.local/share/jarvis/turn_telemetry.db \\
     "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"
   # wait if <60s since last turn
   systemctl --user daemon-reload && \\
     systemctl --user restart jarvis-voice-agent.service"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    """Argparse-free CLI dispatcher.

    Subcommands:
      list [--all]
      show <id>
      merge <id>
      reject <id> <reason...>
      revert <sha>

    Returns 0 on success, 1 on operational error, 2 on usage error.
    """
    if len(argv) < 2:
        print(
            "usage: jarvis-automod "
            "<list|show|merge|deploy|publish|reject|revert> [args]",
            file=sys.stderr,
        )
        return 2

    cmd = argv[1]

    if cmd == "list":
        rows = cmd_list(only_pending="--all" not in argv)
        _print_list_rows(rows)
        return 0

    if cmd == "show":
        if len(argv) < 3:
            print("usage: jarvis-automod show <id>", file=sys.stderr)
            return 2
        try:
            art = cmd_show(argv[2])
        except (OSError, FileNotFoundError, json.JSONDecodeError) as e:
            print(f"artifact not found: {e}", file=sys.stderr)
            return 1
        print(json.dumps(art, indent=2))
        return 0

    if cmd == "merge":
        if len(argv) < 3:
            print("usage: jarvis-automod merge <id>", file=sys.stderr)
            return 2
        ok, info = cmd_merge(argv[2])
        if ok:
            print(f"Merged. SHA: {info}")
            print(_RESTART_GUIDANCE)
            return 0
        print(f"Merge failed: {info}", file=sys.stderr)
        return 1

    if cmd == "deploy":
        # The APPROVED-evolution path: ff-merge + arm the deploy watchdog +
        # restart. Unlike bare `merge` (which only stages + tells you to restart),
        # `deploy` records a rollback point and hands off to
        # jarvis-evolution-watchdog, which auto-rolls-back if the new code is
        # unhealthy. Refuses on a dirty tree (so rollback can't lose data).
        if len(argv) < 3:
            print("usage: jarvis-automod deploy <id>", file=sys.stderr)
            return 2
        from pipeline.automod.deploy import deploy as _do_deploy
        ok, info = _do_deploy(argv[2])
        if ok:
            print(
                f"Deployed {argv[2]} (merge {info[:8]}). The watchdog is now "
                "verifying health and will auto-roll-back if it's unhealthy."
            )
            return 0
        print(f"Deploy refused/failed: {info}", file=sys.stderr)
        return 1

    if cmd == "publish":
        # Push the proposal branch + open a GitHub PR with the summary, so it
        # can be reviewed remotely (the "upload to branches" step). Outward-
        # facing. `--no-draft` opens a ready PR; default is a draft.
        if len(argv) < 3:
            print("usage: jarvis-automod publish <id> [--no-draft]",
                  file=sys.stderr)
            return 2
        from pipeline.automod.publish import publish as _do_publish
        ok, info = _do_publish(argv[2], draft="--no-draft" not in argv)
        if ok:
            print(f"Published. PR: {info}")
            return 0
        print(f"Publish failed: {info}", file=sys.stderr)
        return 1

    if cmd == "reject":
        if len(argv) < 4:
            print("usage: jarvis-automod reject <id> <reason>", file=sys.stderr)
            return 2
        cmd_reject(argv[2], " ".join(argv[3:]))
        print("Rejected.")
        return 0

    if cmd == "revert":
        if len(argv) < 3:
            print("usage: jarvis-automod revert <automod-id-or-sha>",
                  file=sys.stderr)
            return 2
        # Route through revert(args) so an automod ID gets the
        # rollback-ref path; a SHA still hits cmd_revert via the
        # legacy fallback inside revert().
        class _Args:
            pass
        ns = _Args()
        ns.target = argv[2]
        return revert(ns)

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
