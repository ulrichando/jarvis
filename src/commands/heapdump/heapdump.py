"""Heapdump command implementation."""

from __future__ import annotations

import gc
import tracemalloc
from pathlib import Path
from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Dump memory information for debugging."""
    gc.collect()
    desktop = Path.home() / "Desktop"
    output_path = desktop / "heapdump.txt"

    snapshot = None
    if tracemalloc.is_tracing():
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics("lineno")[:20]
        lines = [str(stat) for stat in top_stats]
        output_path.write_text("\n".join(lines))
        return {"type": "text", "value": f"Heap dump written to {output_path}"}

    return {"type": "text", "value": "tracemalloc not active. Start with tracemalloc.start()"}
