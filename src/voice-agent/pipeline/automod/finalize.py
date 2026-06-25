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
import os
import subprocess
import sys
import time
from pathlib import Path

# When invoked as a script, ensure the voice-agent root is on sys.path.
_VOICE_AGENT_ROOT = Path(__file__).resolve().parents[2]
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from pipeline.automod import artifact, test_gate
from pipeline.automod import criteria
from pipeline.automod._state import artifact_path, intent_file_path

logger = logging.getLogger("jarvis.automod.finalize")

# Presence check for the changed-line coverage gate (coverage_gate.py). When
# available, _rerun_pytest runs the suite under `coverage run` so the gate can
# read .coverage; absent → plain pytest and the gate records 'skipped'.
try:
    import coverage as _coverage  # noqa: F401
    _HAS_COVERAGE = True
except Exception:  # noqa: BLE001
    _HAS_COVERAGE = False


def _git(*args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          check=False, **kw)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _base_ref() -> str:
    """The ref the disposable worktree branched from — finalize MUST diff against
    the SAME ref or it double-counts the divergence (that mismatch is what
    produced the bogus `too_many_files` rejections). Defaults to local `master`
    (the current code), which is where the worktree is now based; origin/master
    is intentionally stale here (local is 32 commits ahead). Override with
    JARVIS_AUTOMOD_BASE_REF."""
    return os.environ.get("JARVIS_AUTOMOD_BASE_REF", "master")


def _read_intent(automod_id: str) -> dict:
    """Read the intent file written by the wrapper script. Returns a
    dict with lower-cased keys (intent, rationale, kind, evolution)."""
    p = intent_file_path(automod_id)
    if not p.exists():
        return {"intent": "(missing intent file)"}
    out: dict = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip().lower()
            value = v.strip()
            # Multi-line INTENT bodies (retries) appear last; don't let their
            # body lines clobber fields already parsed above.
            if key in out and key in ("intent", "attempt", "lineage", "prior_failures"):
                continue
            if key in ("evolution", "prior_failures"):
                try:
                    out[key] = json.loads(value)
                except json.JSONDecodeError:
                    out[key] = {} if key == "evolution" else []
            else:
                out[key] = value
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
    tooling_root = Path(os.environ.get(
        "JARVIS_AUTOMOD_TOOLING_ROOT",
        str(Path(__file__).resolve().parents[4]),
    ))
    python = tooling_root / "src" / "voice-agent" / ".venv" / "bin" / "python"
    # Run under `coverage run` (when available) so coverage_gate can read the
    # .coverage data file. argv[0] stays the tooling python either way.
    if _HAS_COVERAGE:
        cmd = [str(python), "-m", "coverage", "run", "-m", "pytest",
               "tests/", "-q", "--tb=no"]
    else:
        cmd = [str(python), "-m", "pytest", "tests/", "-q", "--tb=no"]
    try:
        proc = subprocess.run(
            cmd,
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
    evolution = intent_record.get("evolution")
    if not isinstance(evolution, dict) or not evolution.get("criteria_version"):
        evolution = criteria.enrich_record({
            "id": automod_id,
            "kind": intent_record.get("kind", "unknown"),
            "intent": intent,
            "rationale": intent_record.get("rationale", ""),
        })["evolution"]

    # Retry-lineage fields (learn-and-retry loop). Carried into every artifact so
    # the retry scanner can cap attempts and avoid repeating the same fix.
    try:
        _attempt = int(intent_record.get("attempt", 1) or 1)
    except (TypeError, ValueError):
        _attempt = 1
    _lineage = {
        "attempt": _attempt,
        "lineage": intent_record.get("lineage") or automod_id,
        "prior_failures": intent_record.get("prior_failures") or [],
        "priority": intent_record.get("priority") or "P3",
    }

    # 1. Did a commit land? Compare against the worktree's actual base ref.
    base = _base_ref()
    parent = _git("rev-parse", base).stdout.strip()
    head = _git("rev-parse", "HEAD").stdout.strip()
    if not head or head == parent:
        # No commit landed. HEAD == base here, so the worktree sits AT the base
        # commit — re-running the suite now exercises the BASE, not the proposal.
        # If the base is already RED, the coding agent correctly refused to build
        # on a broken tree; that is NOT this proposal's fault. Filing it as a
        # generic no_commit_landed is what hid the real problem on 2026-06-23:
        # one pre-existing red test silently failed ×7 builds for a day. Detect
        # it and surface a distinct, loud signal so /evolution shows the loop is
        # blocked at the root, not failing per-proposal. (skip_test_rerun keeps
        # the unit tests fast + base-suite check off the no-op path.)
        reason = "no_commit_landed"
        base_tail = ""
        if not skip_test_rerun:
            base_green, base_tail = _rerun_pytest()
            if not base_green:
                reason = "base_suite_red"
        art = {
            "id": automod_id,
            "intent": intent,
            "branch": branch,
            "parent_sha": parent,
            "head_sha": head or "(none)",
            "files_changed": [],
            "diff_summary": "",
            "test_output_tail": base_tail,
            "status": "failed",
            "rejection_reason": reason,
            "evolution": evolution,
            **_lineage,
            "created_at": _now_iso(),
        }
        artifact.write(art)
        artifact.audit("automod_failed", id=automod_id, reason=reason)
        if reason == "base_suite_red":
            # Distinct, dashboard-visible event (route.ts readAuditActivity
            # surfaces automod_* kinds): the base suite is broken — fix it to
            # unblock ALL builds. Without this the loop churns no_commit_landed
            # silently and nobody knows why every proposal "fails".
            artifact.audit("automod_base_suite_red", id=automod_id,
                           detail=base_tail[-400:])
        _delete_branch(branch)
        return art

    # 2. Compute diff against the base the worktree branched from.
    diff_text = _git("diff", f"{base}..HEAD").stdout

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
            "evolution": evolution,
            **_lineage,
            "created_at": _now_iso(),
        }
        artifact.write(art)
        artifact.audit("automod_failed", id=automod_id, reason=reason)
        _delete_branch(branch)
        return art

    # 4. Optional pytest re-run (under coverage, for the changed-line gate).
    test_tail = "(skipped)"
    coverage_result: dict = {
        "status": "skipped", "reason": "test re-run skipped",
        "score": None, "covered": 0, "measurable": 0, "files": {},
    }
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
                "evolution": evolution,
                **_lineage,
                "created_at": _now_iso(),
            }
            artifact.write(art)
            artifact.audit("automod_failed", id=automod_id,
                           reason="tests_failed_on_rerun")
            _delete_branch(branch)
            return art

        # 4b. Changed-line coverage gate. Advisory by default (records the score
        # on the artifact). In enforce mode a low score fails the proposal — the
        # agent added code the suite never exercises. Never raises.
        try:
            from pipeline.automod import coverage_gate
            coverage_result = coverage_gate.evaluate(
                diff_text, cwd=Path(__file__).resolve().parents[2])
        except Exception as e:  # noqa: BLE001 — never break finalize on the gate
            coverage_result = {"status": "error", "reason": str(e)[:200],
                               "score": None, "covered": 0, "measurable": 0,
                               "files": {}}
        if (os.environ.get("JARVIS_AUTOMOD_COVERAGE_GATE", "advisory") == "enforce"
                and coverage_result.get("score") is not None):
            try:
                min_score = float(os.environ.get("JARVIS_AUTOMOD_COVERAGE_MIN", "0.7"))
            except ValueError:
                min_score = 0.7
            if coverage_result["score"] < min_score:
                art = {
                    "id": automod_id,
                    "intent": intent,
                    "branch": branch,
                    "parent_sha": parent,
                    "head_sha": head,
                    "files_changed": test_gate.files_changed(diff_text),
                    "diff_summary": "coverage-insufficient",
                    "test_output_tail": test_tail,
                    "coverage_gate": coverage_result,
                    "status": "failed",
                    "rejection_reason": "coverage_insufficient",
                    "evolution": evolution,
                    **_lineage,
                    "created_at": _now_iso(),
                }
                artifact.write(art)
                artifact.audit("automod_failed", id=automod_id,
                               reason="coverage_insufficient")
                _delete_branch(branch)
                return art

    # 5. Write pending artifact. Persist a truncated unified diff so the
    # /evolution review UI can show it without shelling git (and so it survives
    # the branch being deleted on merge/reject). Capped to keep artifacts small.
    art = {
        "id": automod_id,
        "intent": intent,
        "branch": branch,
        "parent_sha": parent,
        "head_sha": head,
        "files_changed": test_gate.files_changed(diff_text),
        "diff_summary": _git("diff", "--shortstat", f"{base}..HEAD").stdout.strip(),
        "diff": diff_text[:60000],
        "diff_truncated": len(diff_text) > 60000,
        "test_output_tail": test_tail,
        "coverage_gate": coverage_result,
        "status": "pending",
        "evolution": evolution,
        **_lineage,
        "created_at": _now_iso(),
    }
    artifact.write(art)
    artifact.audit("automod_committed", id=automod_id, head_sha=head)
    # Count against the daily cap ONLY now that the proposal is reviewable. A
    # failed build (no commit / tests red / rejected diff) returns earlier and
    # never reaches here, so it never consumes the budget (user 2026-06-23:
    # "only count it if it's successful for review"). Best-effort.
    try:
        from pipeline.automod import throttle
        throttle.mark_admitted(automod_id)
    except Exception:  # noqa: BLE001 — cap accounting must never break finalize
        pass
    # Proposal is now reviewable → notify (sub-project C). Best-effort; the audit
    # event also lets the /evolution activity feed surface it.
    try:
        from pipeline.automod import notify
        notify.notify_proposal_ready(automod_id, intent)
        artifact.audit("automod_proposal_ready", id=automod_id)
    except Exception:  # noqa: BLE001 — never break finalize on a notify failure
        pass
    # 3-lens review council (2026-06-25): correctness / security / regression
    # review the diff and write <id>.review.json, surfaced in the /evolution
    # Review tab BEFORE the human decides to deploy. ADVISORY ONLY — it never
    # gates; the human still approves. Best-effort; disable with
    # JARVIS_AUTOMOD_REVIEW_COUNCIL=0.
    if os.environ.get("JARVIS_AUTOMOD_REVIEW_COUNCIL", "1") != "0":
        try:
            from pipeline.automod import review_council
            review_council.review_proposal(automod_id, diff_text, intent)
        except Exception:  # noqa: BLE001 — advisory; must never break finalize
            pass
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
