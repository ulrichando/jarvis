"""Mem0 memory backend — registers a credentialed fact-extraction provider.

Present + key-gated only. The lean provider exposes ``name`` + ``is_available()``
(gated on ``MEM0_API_KEY``) and inherits safe-default recall/sync ops from
``tools.memory_providers.MemoryProvider``. The voice agent's turn loop uses
file-backed memory and has no consumer for these ops yet (deferred).
"""
from __future__ import annotations

import os

from tools.memory_providers import MemoryProvider


class Mem0MemoryProvider(MemoryProvider):
    name = "mem0"

    def is_available(self) -> bool:
        return bool(os.environ.get("MEM0_API_KEY", "").strip())


def register(ctx) -> None:
    ctx.register_memory_provider(Mem0MemoryProvider())
