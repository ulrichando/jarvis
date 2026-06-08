"""Finalize step for the auto-mod wrapper (Spec B, Plane 3).

Called after `bin/jarvis-automod-impl` has run `bin/jarvis -p` and
either committed or failed. This module:
  1. Verifies a commit landed (HEAD differs from master)
  2. Computes the diff vs master
  3. Validates the diff via pipeline.automod.test_gate.validate_diff
  4. Optionally re-runs pytest (server-side belt+suspenders)
  5. Writes the artifact JSON with status pending/failed
  6. On failure: deletes the branch + writes rejection_reason
  7. Restores master checkout

Usage from shell:
  python finalize.py <id> <branch-name>

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

# When invoked as a script, ensure the voice-agent root is on sys.path.
_VOICE_AGENT_ROOT = Path(__file__).resolve().parents[2]
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from pipeline.automod import artifact, test_gate
from pipeline.automod._state import artifact_path, intent_file_path

logger = logging.getLogger("jarvis.automod.finalize")


def _git(*args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=False, **kw)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_intent(automod_id: str) -> dict:
    """Read the intent file written by the wrapper script. Returns a
    dict with lower-cased keys (intent, rationale, kind)."""
    p = intent_file_path(automod_id)
    if not p.exists():
        return {"intent": "(missing intent file)"}
    out: dict = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _rerun_pytest() -> tuple[bool, str]:
    """Re-run the full test suite in src/voice-agent/. Returns (ok, tail)."""
    # Derive from this file's location (.../src/voice-agent/pipeline/automod/
    # finalize.py → parents[2] == src/voice-agent) so the re-run works on any
    # checkout, not just one hardcoded machine path. Previously this always
    # returned "voice-agent dir missing" off-machine, failing every artifact.
    cwd = Path(__file__).resolve().parents[2]
    if not cwd.exists():
        return False, "voice-agent dir missing"
    try:
        proc = subprocess.run(
            [str(cwd / ".venv" / "bin" / "python"), "-m", "pytest",
             "tests/", "-q", "--tb=no"],
            cwd=cwd, capture_output=True, text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "pytest re-run timed out after 300s"
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-20:]
    return proc.returncode == 0, "\n".join(tail)


def _delete_branch(branch: str) -> None:
    _git("checkout", "master")
    _git("branch", "-D", branch)


def finalize_branch(automod_id: str, branch: str,
                    *, skip_test_rerun: bool = False) -> dict:
    """Validate the auto-mod branch + write artifact. Returns the artifact dict."""
    intent_record = _read_intent(automod_id)
    intent = intent_record.get("intent", "")

    # 1. Did a commit land?
    parent = _git("rev-parse", "master").stdout.strip()
    head = _git("rev-parse", "HEAD").stdout.strip()
    if not head or head == parent:
        art = {
            "id": automod_id,
            "intent": intent,
            "branch": branch,
            "parent_sha": parent,
            "head_sha": head or "(none)",
            "files_changed": [],
            "diff_summary": "",
            "test_output_tail": "",
            "status": "failed",
            "rejection_reason": "no_commit_landed",
            "created_at": _now_iso(),
        }
        artifact.write(art)
        artifact.audit("automod_failed", id=automod_id,
                       reason="no_commit_landed")
        _delete_branch(branch)
        return art

    # 2. Compute diff.
    diff_text = _git("diff", "master..HEAD").stdout

    # 3. Validate diff.
    ok, reason = test_gate.validate_diff(diff_text)
    if not ok:
        art = {
            "id": automod_id,
            "intent": intent,
            "branch": branch,
            "parent_sha": parent,
            "head_sha": head,
            "files_changed": test_gate.files_changed(diff_text),
            "diff_summary": "rejected",
            "test_output_tail": "",
            "status": "failed",
            "rejection_reason": reason,
            "created_at": _now_iso(),
        }
        artifact.write(art)
        artifact.audit("automod_failed", id=automod_id, reason=reason)
        _delete_branch(branch)
        return art

    # 4. Optional pytest re-run.
    test_tail = "(skipped)"
    if not skip_test_rerun:
        green, test_tail = _rerun_pytest()
        if not green:
            art = {
                "id": automod_id,
                "intent": intent,
                "branch": branch,
                "parent_sha": parent,
                "head_sha": head,
                "files_changed": test_gate.files_changed(diff_text),
                "diff_summary": "tests-red",
                "test_output_tail": test_tail,
                "status": "failed",
                "rejection_reason": "tests_failed_on_rerun",
                "created_at": _now_iso(),
            }
            artifact.write(art)
            artifact.audit("automod_failed", id=automod_id,
                           reason="tests_failed_on_rerun")
            _delete_branch(branch)
            return art

    # 5. Write pending artifact.
    art = {
        "id": automod_id,
        "intent": intent,
        "branch": branch,
        "parent_sha": parent,
        "head_sha": head,
        "files_changed": test_gate.files_changed(diff_text),
        "diff_summary": _git("diff", "--shortstat", "master..HEAD").stdout.strip(),
        "test_output_tail": test_tail,
        "status": "pending",
        "created_at": _now_iso(),
    }
    artifact.write(art)
    artifact.audit("automod_committed", id=automod_id, head_sha=head)
    _git("checkout", "master")
    return art


def mark_auto_merged(
    automod_id: str,
    rollback_ref: str,
    rollback_sha: str,
    merge_sha: str,
) -> None:
    """Stamp an automod artifact JSON with auto-merge metadata.

    Idempotent — overwrites the auto_merged_at + rollback fields on
    repeat calls. If no artifact exists yet (wrapper crashed before
    normal finalize ran), creates a minimal record so the revert path
    can find it. Spec 2026-05-28."""
    artifact_file = artifact_path(automod_id)
    if artifact_file.exists():
        try:
            record = json.loads(artifact_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {"id": automod_id}
    else:
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        record = {"id": automod_id}

    record["auto_merged_at"] = _now_iso()
    record["rollback_ref"] = rollback_ref
    record["rollback_sha"] = rollback_sha
    record["merge_sha"] = merge_sha
    artifact_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
    try:
        artifact.audit(
            "automod_auto_merged",
            id=automod_id,
            rollback_ref=rollback_ref,
            rollback_sha=rollback_sha,
            merge_sha=merge_sha,
        )
    except Exception:
        pass  # audit failures must never break the flow


if __name__ == "__main__":
    import argparse

    # Legacy invocation: `python finalize.py <id> <branch>` (no subcommand).
    # Detect by checking whether the first argument looks like a subcommand
    # keyword rather than an automod ID.
    _SUBCOMMANDS = {"finalize", "mark-auto-merged"}
    if len(sys.argv) >= 2 and not sys.argv[1].startswith("-") \
            and sys.argv[1] not in _SUBCOMMANDS:
        # Legacy path — preserve exact existing behaviour.
        if len(sys.argv) < 3:
            print("usage: finalize.py <id> <branch>", file=sys.stderr)
            sys.exit(2)
        result = finalize_branch(sys.argv[1], sys.argv[2])
        print(json.dumps(result, indent=2))
        sys.exit(0)

    parser = argparse.ArgumentParser(
        prog="finalize",
        description="Auto-mod finalize utilities",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ------------------------------------------------------------------
    # finalize <id> <branch>  — same as the legacy positional invocation
    # ------------------------------------------------------------------
    fp = sub.add_parser(
        "finalize",
        help="Validate auto-mod branch and write artifact (default action)",
    )
    fp.add_argument("id", help="automod artifact ID")
    fp.add_argument("branch", help="git branch name")
    fp.set_defaults(handler=lambda a: (
        print(json.dumps(finalize_branch(a.id, a.branch), indent=2))
    ))

    # ------------------------------------------------------------------
    # mark-auto-merged <id> --rollback-ref ... --rollback-sha ... --merge-sha ...
    # Auto-merge stamping subcommand (Spec 2026-05-28).
    # ------------------------------------------------------------------
    mp = sub.add_parser(
        "mark-auto-merged",
        help="Stamp artifact with auto-merge metadata (called by wrapper)",
    )
    mp.add_argument("id", help="automod artifact ID")
    mp.add_argument("--rollback-ref", required=True,
                    help="git ref created for one-step revert")
    mp.add_argument("--rollback-sha", required=True,
                    help="SHA the rollback ref points to")
    mp.add_argument("--merge-sha", required=True,
                    help="merge commit SHA on master")
    mp.set_defaults(handler=lambda a: mark_auto_merged(
        a.id, a.rollback_ref, a.rollback_sha, a.merge_sha
    ))

    args = parser.parse_args()
    args.handler(args)
