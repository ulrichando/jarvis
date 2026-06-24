"""Deploy actuator + deploy-watch marker for the self-evolution loop.

The deploy step is the single dangerous moment: JARVIS restarts into newly
self-written code. To make a bad deploy SURVIVABLE, every deploy:

  1. asserts a CLEAN working tree (so the watchdog's `git reset --hard` rollback
     can never destroy uncommitted work),
  2. captures the current master HEAD as the rollback target (last-good SHA),
  3. ff-merges the approved branch (via cli.cmd_merge — the 3rd defence layer),
  4. writes an ACTIVE-DEPLOY MARKER (rollback_sha + deadline),
  5. restarts the service (respecting the 60s-since-last-turn guard).

An EXTERNAL watchdog (``pipeline.automod.watchdog``, driven by a systemd --user
timer that is a SEPARATE process from the agent) then verifies health inside the
window and auto-rolls-back to ``rollback_sha`` if JARVIS doesn't come back
healthy. The agent can't be its own deploy safety net — the failure mode is "he's
dead and can't fix himself" — so the watchdog lives outside his process.

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pipeline.automod._state import _automod_home, artifact_path

# Repo root: this file is src/voice-agent/pipeline/automod/deploy.py
REPO_ROOT = Path(__file__).resolve().parents[4]

# How long the watchdog waits for a healthy turn before rolling back. 5 min
# covers a slow restart + model warmup + one smoke-turn.
DEFAULT_DEADLINE_S = int(os.environ.get("JARVIS_EVOLUTION_DEPLOY_DEADLINE_S", "300"))

_TELEMETRY_DB = Path(
    os.environ.get("JARVIS_TELEMETRY_PATH")
    or (Path.home() / ".local/share/jarvis/turn_telemetry.db")
)


def marker_path() -> Path:
    return _automod_home() / "active-deploy.json"


def read_marker() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_marker(marker: Dict[str, Any]) -> None:
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomic


def clear_marker() -> None:
    try:
        marker_path().unlink()
    except FileNotFoundError:
        pass


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT),
        capture_output=True, text=True, check=False,
    )


def tree_is_clean() -> bool:
    """True if the working tree has no uncommitted changes."""
    return _git("status", "--porcelain").stdout.strip() == ""


def _proposal_files_dirty(automod_id: str) -> list[str]:
    """Return the proposal's own changed files that ALSO have uncommitted local
    changes — those (and only those) would conflict the ff-merge. Unrelated
    dirty files don't matter: ff-merge leaves them untouched (verified), and the
    watchdog rollback stashes before `git reset --hard`, so they're never
    destroyed. Empty list = safe to deploy even with a dirty tree.

    This replaces the old whole-tree clean requirement, which made deploy
    impossible whenever a parallel session left the repo dirty (2026-06-23)."""
    try:
        art = json.loads(artifact_path(automod_id).read_text(encoding="utf-8"))
        files = set(art.get("files_changed") or [])
    except (OSError, json.JSONDecodeError):
        return []
    if not files:
        return []
    dirty = []
    for line in _git("status", "--porcelain").stdout.splitlines():
        # porcelain: "XY <path>" — 2 status chars + space, then the path.
        path = line[3:].strip().strip('"')
        if path in files:
            dirty.append(path)
    return sorted(dirty)


def _seconds_since_last_turn() -> Optional[float]:
    """Age of the most recent telemetry turn, or None if unknown."""
    try:
        import calendar
        import sqlite3
        with sqlite3.connect(f"file:{_TELEMETRY_DB}?mode=ro", uri=True) as c:
            row = c.execute("SELECT MAX(ts_utc) FROM turns").fetchone()
        if not row or not row[0]:
            return None
        # ts_utc is UTC ('...Z'); timegm → UTC epoch matching time.time().
        last = calendar.timegm(time.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ"))
        return max(0.0, time.time() - last)
    except Exception:
        return None


def _wait_for_quiet(min_gap_s: int = 60, cap_s: int = 75) -> None:
    """Honor the CLAUDE.md rule: don't restart within 60s of the last turn.
    Polls until the gap clears, bounded by cap_s so a deploy never hangs."""
    waited = 0
    while waited < cap_s:
        age = _seconds_since_last_turn()
        if age is None or age >= min_gap_s:
            return
        time.sleep(5)
        waited += 5


def _restart_agent() -> tuple[bool, str]:
    r = subprocess.run(
        ["systemctl", "--user", "restart", "jarvis-voice-agent.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout or f"systemctl exited {r.returncode}").strip()


def deploy(automod_id: str, *, deadline_s: int = DEFAULT_DEADLINE_S) -> tuple[bool, str]:
    """Deploy an APPROVED proposal: clean-tree guard → capture rollback SHA →
    ff-merge → write deploy marker → restart. The watchdog takes it from here.

    Returns (True, merge_sha) or (False, reason). Proceeds even with unrelated
    uncommitted files present, refusing only if the PROPOSAL'S OWN files are
    dirty (which would conflict the ff-merge). The watchdog rollback stashes
    before reset, so a rollback never destroys uncommitted work.
    """
    # Lazy import to avoid a circular import (cli imports deploy for the
    # subcommand; deploy needs cli's merge + audit).
    from pipeline.automod import artifact
    from pipeline.automod.cli import cmd_merge

    dirty_own = _proposal_files_dirty(automod_id)
    if dirty_own:
        return False, (
            "refused: the proposal's own files have uncommitted changes ("
            + ", ".join(dirty_own)
            + ") — commit or stash those so the ff-merge doesn't conflict. "
            "Unrelated dirty files are fine."
        )

    rollback_sha = _git("rev-parse", "HEAD").stdout.strip()
    if not rollback_sha:
        return False, "refused: could not resolve current HEAD (rollback target)"

    ok, info = cmd_merge(automod_id)
    if not ok:
        return False, f"merge_failed:{info}"
    merge_sha = info

    marker = {
        "automod_id": automod_id,
        "merge_sha": merge_sha,
        "rollback_sha": rollback_sha,
        "deployed_at": _now_iso(),
        "deadline_s": int(deadline_s),
        "restart_requested_monotonic": None,
        "state": "pending-restart",
    }
    write_marker(marker)
    try:
        artifact.audit(
            "automod_deploy_started",
            id=automod_id, merge_sha=merge_sha, rollback_sha=rollback_sha,
        )
    except Exception:  # noqa: BLE001
        pass

    _wait_for_quiet()
    marker["restart_requested_monotonic"] = time.monotonic()
    marker["state"] = "watching"
    write_marker(marker)
    restart_ok, restart_detail = _restart_agent()
    if not restart_ok:
        marker["state"] = "restart-failed"
        marker["restart_error"] = restart_detail
        write_marker(marker)
        try:
            artifact.audit(
                "automod_deploy_restart_failed",
                id=automod_id, merge_sha=merge_sha, error=restart_detail,
            )
        except Exception:  # noqa: BLE001
            pass
        return False, f"restart_failed:{restart_detail}"
    return True, merge_sha
