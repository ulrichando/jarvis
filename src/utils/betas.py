"""Beta feature management utilities."""

from __future__ import annotations

from typing import Optional

ALLOWED_SDK_BETAS = ["context-1m"]


def filter_allowed_sdk_betas(
    sdk_betas: Optional[list[str]],
) -> Optional[list[str]]:
    """Filter SDK betas to only include allowed ones."""
    if not sdk_betas:
        return None

    allowed = [b for b in sdk_betas if b in ALLOWED_SDK_BETAS]
    return allowed if allowed else None


def model_supports_isp(model: str) -> bool:
    """Check if model supports interleaved thinking."""
    canonical = model.lower()
    return "claude" in canonical


def model_supports_structured_outputs(model: str) -> bool:
    """Check if model supports structured outputs."""
    return True


def clear_betas_caches() -> None:
    """Clear all beta-related caches."""
    pass
