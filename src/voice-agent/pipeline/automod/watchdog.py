"""External deploy watchdog — the thing that lets JARVIS SURVIVE a bad deploy.

Driven by ``jarvis-evolution-watchdog.timer`` (systemd --user), this runs in a
process SEPARATE from the agent, so it still fires even when the agent is dead —
the whole point. The agent cannot be its own deploy safety net.

Each tick (``run_once``):
  * No active-deploy marker → nothing to do.
  * Marker present, still inside the boot grace → let the agent finish starting.
  * Marker present, within the window → run the HEALTH GATE:
        liveness  (/status: connected + agent_present, service active)
          AND  one successful turn  (a real post-deploy turn OR a smoke-turn)
    → PASS: confirm (clear marker, audit, notify "live").
  * Marker present, deadline passed without a healthy gate → ROLL BACK:
        git reset --hard <rollback_sha>  +  restart  → audit + notify, clear marker.

Rollback is non-destructive: any unexpected working-tree changes are stashed
first (a deploy is asserted clean, but never lose data on the emergency path).

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional

from pipeline.automod import deploy as _deploy
from pipeline.automod import fault_boundary
from pipeline.automod._state import evolution_log_path

logger = logging.getLogger("jarvis.automod.watchdog")

# Don't judge the agent until it's had a chance to boot + connect.
BOOT_GRACE_S = 30
# Give up auto-rollback after this many failed attempts (then leave it for a
# human — a rollback that can't apply is worse looped than escalated).
MAX_ROLLBACK_ATTEMPTS = 3

_VC_PORT = __import__("os").environ.get("JARVIS_VOICE_CLIENT_PORT", "8767")


def _parse_iso(ts: str) -> Optional[float]:
    # The stamps are UTC ('...Z'); timegm interprets the struct as UTC so the
    # epoch matches time.time(). (mktime would treat it as local + be DST-buggy.)
    import calendar
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None


# ── health signals ────────────────────────────────────────────────────────

def _service_active() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "jarvis-voice-agent.service"],
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip() == "active"


def _service_active_enter_monotonic() -> Optional[float]:
    """systemd ActiveEnterTimestampMonotonic in seconds, or None if absent."""
    r = subprocess.run(
        [
            "systemctl", "--user", "show", "jarvis-voice-agent.service",
            "-p", "ActiveEnterTimestampMonotonic", "--value",
        ],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return None
    try:
        raw = int((r.stdout or "").strip() or "0")
    except ValueError:
        return None
    if raw <= 0:
        return None
    return raw / 1_000_000.0


def _fresh_service_after(restart_requested_monotonic: Optional[float]) -> bool:
    """True when the service entered active state after deploy requested restart.

    Legacy markers lack this field; keep those compatible by treating freshness as
    satisfied. New deploy markers include it, preventing the watchdog from
    confirming an old still-running agent after a failed restart.
    """
    if not restart_requested_monotonic:
        return True
    active_at = _service_active_enter_monotonic()
    if active_at is None:
        return False
    # Allow a tiny clock/read-order tolerance around the restart request.
    return active_at >= (float(restart_requested_monotonic) - 0.1)


def _liveness() -> bool:
    """Agent process up + connected to the room + present as an agent."""
    if not _service_active():
        return False
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{_VC_PORT}/status", timeout=2
        ) as resp:
            st = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    return bool(st.get("connected")) and bool(st.get("agent_present"))


def _real_turn_since(deployed_at_epoch: float) -> bool:
    """A genuine telemetry turn landed AFTER the deploy and wasn't a hard
    failure. Covers the case where you're actually up and talking post-deploy."""
    try:
        import sqlite3
        db = _deploy._TELEMETRY_DB
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            row = c.execute(
                "SELECT ts_utc, had_tool_error FROM turns "
                "ORDER BY ts_utc DESC LIMIT 1"
            ).fetchone()
        if not row or not row[0]:
            return False
        ts = _parse_iso(row[0])
        if ts is None or ts <= deployed_at_epoch:
            return False
        return not bool(row[1])  # last turn is post-deploy AND not a tool-error
    except Exception:
        return False


def _smoke_turn() -> bool:
    """Run the synthetic smoke-turn as an isolated subprocess (the deployed
    code's brain doing one completion). Exit 0 = ok."""
    try:
        r = subprocess.run(
            [str(_deploy.REPO_ROOT / "src/voice-agent/.venv/bin/python"),
             "-m", "pipeline.automod.selftest"],
            cwd=str(_deploy.REPO_ROOT / "src/voice-agent"),
            capture_output=True, text=True, timeout=45, check=False,
        )
        if r.returncode == 0:
            return True
        logger.info("[watchdog] smoke-turn not-ok (rc=%s): %s",
                    r.returncode, (r.stdout or r.stderr).strip()[:200])
        return False
    except Exception as e:  # noqa: BLE001
        logger.info("[watchdog] smoke-turn error: %s", e)
        return False


# ── rollback ──────────────────────────────────────────────────────────────

def _rollback(rollback_sha: str) -> bool:
    """git reset --hard <rollback_sha> + restart. Stashes any unexpected dirty
    tree first so the emergency path can never destroy uncommitted work."""
    if _deploy._git("status", "--porcelain").stdout.strip():
        # Should be clean (deploy asserted it), but never lose data.
        _deploy._git("stash", "push", "-u", "-m",
                     f"evolution-watchdog-rollback-{int(time.time())}")
    reset = _deploy._git("reset", "--hard", rollback_sha)
    if reset.returncode != 0:
        logger.critical("[watchdog] ROLLBACK reset failed: %s", reset.stderr.strip())
        return False
    subprocess.run(
        ["systemctl", "--user", "restart", "jarvis-voice-agent.service"],
        check=False,
    )
    return True


def _notify(event: str, **fields) -> None:
    """Append a notification record the web /evolution view (Phase 2/4) surfaces,
    and log it. Best-effort — never raises."""
    rec = {"ts": _deploy._now_iso(), "event": event, **fields}
    try:
        logger.warning("[watchdog] %s %s", event, json.dumps(fields))
    except Exception:  # noqa: BLE001
        pass
    try:
        p = evolution_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


# ── main tick ───────────────────────────────────────────────────────────

@fault_boundary.supervised("watchdog_run_once", fallback="crashed")
def run_once() -> str:
    """One watchdog tick. Returns a short status string (also for tests)."""
    marker = _deploy.read_marker()
    if not marker:
        return "no-marker"

    automod_id = marker.get("automod_id", "?")
    rollback_sha = marker.get("rollback_sha")
    deployed_at = _parse_iso(marker.get("deployed_at", "")) or time.time()
    deadline_s = int(marker.get("deadline_s", _deploy.DEFAULT_DEADLINE_S))
    has_restart_field = "restart_requested_monotonic" in marker
    restart_requested = marker.get("restart_requested_monotonic")
    try:
        restart_requested = float(restart_requested) if restart_requested else None
    except (TypeError, ValueError):
        restart_requested = None
    elapsed = time.time() - deployed_at

    if elapsed < BOOT_GRACE_S:
        return "boot-grace"

    # New deploy markers are written before the quiet-wait + restart so the
    # external watchdog knows a deploy is in flight. Do not confirm health
    # against the old process during that pre-restart window.
    if has_restart_field and restart_requested is None and elapsed <= deadline_s:
        return "waiting-restart"

    # Health gate: liveness AND one successful turn.
    if (
        not (has_restart_field and restart_requested is None)
        and _liveness()
        and _fresh_service_after(restart_requested)
        and (_real_turn_since(deployed_at) or _smoke_turn())
    ):
        _deploy.clear_marker()
        try:
            from pipeline.automod import artifact
            artifact.audit("automod_deploy_confirmed", id=automod_id)
        except Exception:  # noqa: BLE001
            pass
        _notify("evolution_deployed", automod_id=automod_id,
                detail="healthy after deploy")
        logger.info("[watchdog] deploy %s CONFIRMED healthy", automod_id)
        # #15: reflect the confirmed-healthy deploy on GitHub (push origin/master
        # + a closed Issue for the shipped fix). Gated + best-effort — a GitHub
        # hiccup must never affect the rollback-safety path above.
        if os.environ.get("JARVIS_EVOLUTION_GITHUB_DEPLOY", "0") == "1":
            try:
                from pipeline.automod import artifact, publish
                ok, info = publish.publish_deploy(automod_id)
                artifact.audit(
                    "automod_deploy_published" if ok else "automod_deploy_publish_failed",
                    id=automod_id, info=(info or "")[:200])
            except Exception as e:  # noqa: BLE001
                logger.warning("[watchdog] github deploy-publish failed for %s: %s",
                               automod_id, e)
        return "confirmed"

    # Not healthy yet. Still inside the window → wait for the next tick.
    if elapsed <= deadline_s:
        return "watching"

    # Deadline blown without a healthy turn → roll back.
    attempts = int(marker.get("rollback_attempts", 0)) + 1
    if not rollback_sha:
        _notify("evolution_rollback_impossible", automod_id=automod_id,
                detail="marker has no rollback_sha")
        _deploy.clear_marker()
        return "rollback-impossible"
    if attempts > MAX_ROLLBACK_ATTEMPTS:
        _notify("evolution_rollback_giving_up", automod_id=automod_id,
                attempts=attempts, detail="manual intervention needed")
        logger.critical("[watchdog] rollback of %s failed %d times — escalating",
                        automod_id, attempts - 1)
        _deploy.clear_marker()
        return "rollback-gave-up"

    marker["rollback_attempts"] = attempts
    marker["state"] = "rolling-back"
    _deploy.write_marker(marker)
    logger.critical(
        "[watchdog] deploy %s UNHEALTHY past %ds — rolling back to %s (attempt %d)",
        automod_id, deadline_s, rollback_sha[:8], attempts,
    )
    if _rollback(rollback_sha):
        try:
            from pipeline.automod import artifact
            artifact.update_status(automod_id, "auto-rolled-back",
                                   rolled_back_at=_deploy._now_iso(),
                                   rollback_sha=rollback_sha)
            artifact.audit("automod_deploy_rolled_back", id=automod_id,
                           rollback_sha=rollback_sha)
        except Exception:  # noqa: BLE001
            pass
        _notify("evolution_rolled_back", automod_id=automod_id,
                rollback_sha=rollback_sha[:8],
                detail="deploy was unhealthy; reverted to last-good + restarted")
        _deploy.clear_marker()
        return "rolled-back"
    # Reset failed — keep the marker so the next tick retries.
    return "rollback-failed"


def main() -> int:
    print(run_once())
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
