"""Context window management utilities."""

from __future__ import annotations

import os
from typing import Optional

MODEL_CONTEXT_WINDOW_DEFAULT = 200_000
COMPACT_MAX_OUTPUT_TOKENS = 20_000
MAX_OUTPUT_TOKENS_DEFAULT = 32_000
CAPPED_DEFAULT_MAX_TOKENS = 8_000
ESCALATED_MAX_TOKENS = 64_000


def is_1m_context_disabled() -> bool:
    """Check if 1M context is disabled via environment variable."""
    return os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT", "").lower() in (
        "1", "true", "yes"
    )


def has_1m_context(model: str) -> bool:
    """Check if a model string indicates 1M context."""
    if is_1m_context_disabled():
        return False
    return "[1m]" in model.lower()


def model_supports_1m(model: str) -> bool:
    """Check if a model supports 1M context."""
    if is_1m_context_disabled():
        return False
    canonical = model.lower()
    return "claude-sonnet-4" in canonical or "opus-4-6" in canonical


def get_context_window_for_model(
    model: str, betas: Optional[list[str]] = None
) -> int:
    """Get the context window size for a model."""
    override = os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS")
    if override:
        try:
            val = int(override)
            if val > 0:
                return val
        except ValueError:
            pass

    if has_1m_context(model):
        return 1_000_000

    return MODEL_CONTEXT_WINDOW_DEFAULT
