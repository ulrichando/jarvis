#!/usr/bin/env python3
"""One-shot scheduler tick — invoked every minute by jarvis-cron.timer.

This runs independently of the LiveKit voice daemon, so scheduled jobs fire
even when no voice session is connected (truly unattended). The LiveKit
`entrypoint` is per-session, so it cannot host an always-on tick; this
systemd-timer worker is the always-on ticker instead.

Env (GROQ_API_KEY etc., needed for prompt jobs) is provided by the
jarvis-cron.service EnvironmentFile directives; the working directory is
src/voice-agent so `pipeline.*` imports resolve. Delivery from this process
is notify-send + the pending queue (there is no live session here) — the
voice agent drains + voices the queue on the user's next connect.
"""
import asyncio
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("jarvis.cron_worker")


def main() -> None:
    if os.environ.get("JARVIS_CRON_DISABLED") == "1":
        logger.info("cron disabled via JARVIS_CRON_DISABLED=1 — skipping tick")
        return
    from pipeline import cron_scheduler as cron
    asyncio.run(cron.tick())


if __name__ == "__main__":
    main()
