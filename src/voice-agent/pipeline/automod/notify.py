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
import shutil
import subprocess

logger = logging.getLogger("jarvis.automod.notify")

_TITLE = "JARVIS — proposal ready for review"


def notify_proposal_ready(automod_id: str, intent: str) -> bool:
    """Best-effort desktop notification for a newly-reviewable proposal.

    Returns True if a notifier was invoked, False otherwise. Never raises.
    """
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
