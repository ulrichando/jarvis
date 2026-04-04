"""Poll PR review status periodically."""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

POLL_INTERVAL_MS = 60_000
SLOW_GH_THRESHOLD_MS = 4_000
IDLE_STOP_MS = 60 * 60_000  # 60 minutes


@dataclass
class PrStatusState:
    number: Optional[int] = None
    url: Optional[str] = None
    review_state: Optional[str] = None
    last_updated: float = 0


async def fetch_pr_status() -> Optional[dict]:
    """Fetch PR status using gh CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "view", "--json", "number,url,reviewDecision",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            return None

        import json
        data = json.loads(stdout.decode())
        return {
            "number": data.get("number"),
            "url": data.get("url"),
            "review_state": data.get("reviewDecision"),
        }
    except Exception:
        return None


class PrStatusPoller:
    """Polls PR review status periodically.

    Polls every 60s while the session is active. Stops after 60 minutes
    of idle time. Disables permanently if a fetch exceeds 4s.

    Equivalent to usePrStatus React hook.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.state = PrStatusState()
        self._disabled = False
        self._last_fetch: float = 0
        self._last_activity: float = time.time() * 1000
        self._task: Optional[asyncio.Task] = None

    async def poll(self) -> None:
        """Perform a single poll for PR status."""
        if not self.enabled or self._disabled:
            return

        now = time.time() * 1000
        if now - self._last_activity >= IDLE_STOP_MS:
            return

        start = time.time() * 1000
        result = await fetch_pr_status()
        self._last_fetch = start

        if result:
            new_number = result.get("number")
            new_review = result.get("review_state")
            if self.state.number != new_number or self.state.review_state != new_review:
                self.state = PrStatusState(
                    number=new_number,
                    url=result.get("url"),
                    review_state=new_review,
                    last_updated=time.time() * 1000,
                )

        elapsed = time.time() * 1000 - start
        if elapsed > SLOW_GH_THRESHOLD_MS:
            self._disabled = True

    def on_activity(self) -> None:
        """Record user activity to prevent idle timeout."""
        self._last_activity = time.time() * 1000

    async def start_polling(self) -> None:
        """Start the polling loop."""
        while self.enabled and not self._disabled:
            await self.poll()
            await asyncio.sleep(POLL_INTERVAL_MS / 1000)

    def stop(self) -> None:
        """Stop polling."""
        self.enabled = False
        if self._task:
            self._task.cancel()
