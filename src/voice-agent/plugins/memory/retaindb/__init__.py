"""RetainDB memory backend — registers a credentialed cloud-memory provider.

Present + key-gated only. The lean provider exposes ``name`` + ``is_available()``
(gated on ``RETAINDB_API_KEY``) and inherits safe-default recall/sync ops from
``tools.memory_providers.MemoryProvider``. The voice agent's turn loop uses
file-backed memory and has no consumer for these ops yet (deferred).
"""
from __future__ import annotations

import os

from tools.memory_providers import MemoryProvider


class RetainDBMemoryProvider(MemoryProvider):
    name = "retaindb"

    def is_available(self) -> bool:
        return bool(os.environ.get("RETAINDB_API_KEY", "").strip())


def register(ctx) -> None:
    ctx.register_memory_provider(RetainDBMemoryProvider())
