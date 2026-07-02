"""Evolution loop heartbeat + liveness (2026-07-02).

Fixes the "the cycle feels off / isn't it supposed to always run?" gap: in AUTO
the loop is heavily gated (idle 10m / cooldown 60m / $6 budget), so a perfectly
healthy loop does nothing visible for long stretches — silence that reads as
"broken." This module makes the loop's state legible: WHY it isn't building right
now, and WHEN it will.

Two things:
  * ``beat()`` — the in-process loop stamps ``heartbeat.json`` every tick, so
    "loop last ticked 34s ago" proves the asyncio loop is alive (distinct from
    the systemd timers).
  * ``compute_status()`` — derives the gate state (mode / why-idle / cooldown /
    budget) from the SAME signals ``throttle.admit_intent`` gates on, so the UI
    can say "auto · waiting: cooldown 34m" instead of showing nothing. Reuses
    throttle's helpers → one source of truth for the thresholds.

Pure-ish (reads state files), never raises → a degraded ``{state:"unknown"}`` on
any problem. Lives under pipeline/automod/ (auto-mod HARD_BLOCKLIST) — human-edit
only, like the rest of the loop's control plane.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from pipeline.automod._state import (
    _automod_home,
    cycle_marker_path,
    is_auto_mode,
    is_evolution_paused,
)

logger = logging.getLogger("jarvis.automod.heartbeat")


def _heartbeat_path() -> Path:
    return _automod_home() / "heartbeat.json"


def _active_deploy_present() -> bool:
    return (_automod_home() / "active-deploy.json").exists()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _cycle_running() -> bool:
    """True if a build cycle marker names a live pid."""
    m = cycle_marker_path()
    if not m.exists():
        return False
    try:
        pid = int(json.loads(m.read_text(encoding="utf-8")).get("pid", 0) or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return bool(pid) and _pid_alive(pid)


def compute_status() -> dict:
    """The loop's current gate state — WHY it is or isn't building right now.

    Mirrors the gate ORDER in throttle.admit_intent so the explanation matches
    what would actually happen to the next intent. Reuses throttle's helpers for
    the thresholds (single source of truth). Never raises.
    """
    try:
        from pipeline.automod import cost_ledger, throttle

        mode = "auto" if is_auto_mode() else "manual"
        paused = is_evolution_paused()
        idle_s = throttle._idle_seconds()
        idle_min = throttle._idle_minutes()
        cooldown_min = throttle._cooldown_minutes()
        since_build_min = throttle._since_last_build_min()
        spent = cost_ledger.spent_today()
        cap = cost_ledger.daily_usd()
        cooldown_left_s = max(0.0, (cooldown_min - since_build_min) * 60.0)

        # Resolve the single most relevant state, gate order first.
        if paused:
            state, reason = "paused", "evolution is paused"
        elif mode == "manual":
            state, reason = "manual", "manual mode — detecting + queueing, not building"
        elif _active_deploy_present():
            state, reason = "deploying", "a deploy is live — watchdog is verifying health"
        elif _cycle_running():
            state, reason = "building", "a build cycle is running now"
        elif idle_s < idle_min * 60:
            state, reason = "waiting", f"you're active — builds wait for {idle_min}m of quiet"
        elif spent >= cap:
            state, reason = "budget", f"today's build budget is spent (${spent:.2f}/${cap:.0f})"
        elif cooldown_left_s > 0:
            state, reason = "cooldown", f"cooling down — {int(cooldown_left_s / 60)}m until the next build"
        else:
            state, reason = "ready", "gates clear — will build the next queued intent"

        return {
            "mode": mode,
            "paused": paused,
            "state": state,
            "reason": reason,
            "idle_s": round(idle_s, 1) if idle_s < 1e8 else None,
            "cooldown_left_s": round(cooldown_left_s, 1),
            "budget_spent": round(spent, 4),
            "budget_cap": round(cap, 2),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except Exception as e:  # noqa: BLE001 — liveness must never crash the caller
        logger.debug("[heartbeat] compute_status failed: %s", e)
        return {"state": "unknown", "reason": f"status unavailable: {e}"[:120],
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def beat() -> None:
    """Stamp heartbeat.json with the current status. Called every loop tick so
    the UI can show a true 'last ticked Xs ago'. Best-effort; never raises."""
    try:
        p = _heartbeat_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(compute_status()), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        logger.debug("[heartbeat] beat write failed: %s", e)


def read() -> dict | None:
    """Last stamped heartbeat, or None. Best-effort."""
    try:
        return json.loads(_heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    print(json.dumps(compute_status(), indent=2))
