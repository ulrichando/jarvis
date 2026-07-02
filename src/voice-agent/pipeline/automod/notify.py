"""Proposal-ready notification (sub-project C, 2026-06-23).

Fires a best-effort desktop notification the moment a self-evolution proposal
becomes reviewable (finalize writes status=pending), so the human-gated stage
doesn't require watching the /evolution page. Native + zero-dependency: Linux
`notify-send` over the user's session bus. Never raises — notification failure
must never break the finalize flow.

Future channels (voice / web push) can hook the same entry point.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger("jarvis.automod.notify")

_TITLE = "JARVIS — proposal ready for review"


def notify_proposal_ready(automod_id: str, intent: str) -> bool:
    """Best-effort desktop notification for a newly-reviewable proposal.

    Returns True if a notifier was invoked, False otherwise. Never raises.

    A paused evolution cycle suppresses the popup. This is the universal
    chokepoint every proposal notification flows through, so gating it here
    closes the 'pause didn't stop the popups' bug (2026-06-28): the spawner
    already no-ops builds when paused, but a re-finalize/announce path still
    fired desktop notifications past the pause flag.
    """
    # Never fire a REAL notification from inside a test run. The automod
    # finalize/cycle tests call this as a side-effect with throwaway intents
    # ("test-001", "fix X", …) and would spam the desktop — and the phone, via
    # desktop->mobile mirroring — with proposals that never enter the real queue.
    # pytest sets PYTEST_CURRENT_TEST per test and child finalize subprocesses
    # inherit it, so this one check covers in-process and subprocess callers.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        from pipeline.automod._state import is_evolution_paused
        if is_evolution_paused():
            logger.debug("[automod] evolution paused — suppressing proposal notification")
            return False
    except Exception:  # noqa: BLE001 — the gate must never break notify
        pass
    body = f"{(intent or '').strip()[:160]}\n({automod_id}) — review at /evolution"
    notifier = shutil.which("notify-send")
    if not notifier:
        logger.debug("[automod] notify-send not found; skipping desktop notify")
        return False
    try:
        subprocess.run(
            [notifier, "-a", "JARVIS", "-u", "normal", _TITLE, body],
            check=False,
            timeout=5,
            capture_output=True,
        )
        return True
    except Exception as e:  # noqa: BLE001 — notification must never break finalize
        logger.debug("[automod] desktop notify failed: %s", e)
        return False
