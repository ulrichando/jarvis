"""model-providers/alibaba — present for upstream parity; inert in the JARVIS voice substrate.

Contributes a model-provider profile, which the voice agent has no consumer for. It
is shipped so the plugin set mirrors upstream and discovery is exercised;
register() intentionally contributes nothing here.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    logger.debug(
        "plugin %s present but inert (no voice consumer for its contribution type)",
        getattr(getattr(ctx, "manifest", None), "name", "?"),
    )
