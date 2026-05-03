"""One-shot: read each watched settings file once and publish the
current value as a settings.value.changed event. Idempotent — the
daemon's UPSERT collapses repeats.

Usage:
    PYTHONPATH=src python -m hub.migrate_settings
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

try:
    from hub import settings_watcher  # absolute when invoked via -m
except ImportError:
    import settings_watcher  # type: ignore[no-redef]


async def run() -> int:
    home = Path.home()
    watched = {
        "cli-model":    home / ".jarvis" / "cli-model",
        "voice-model":  home / ".jarvis" / "voice-model",
        "tts-provider": home / ".jarvis" / "tts-provider",
    }

    import redis.asyncio as aredis
    redis = aredis.from_url(
        os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379"),
        decode_responses=True,
    )
    state: dict[str, str] = {}
    n = await settings_watcher.scan_once(redis, watched, state)
    await redis.aclose()
    return n


def main() -> None:
    n = asyncio.run(run())
    print(f"published {n} settings.value.changed events")


if __name__ == "__main__":
    main()
