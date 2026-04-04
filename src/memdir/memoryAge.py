"""Memory age tracking and decay utilities."""

from __future__ import annotations

import time


def compute_age_hours(created_at: float) -> float:
    """Compute age in hours from a timestamp."""
    return (time.time() * 1000 - created_at) / (1000 * 60 * 60)


def compute_decay_factor(age_hours: float, half_life_hours: float = 168.0) -> float:
    """Compute exponential decay factor. Default half-life is 1 week."""
    import math
    return math.pow(0.5, age_hours / half_life_hours)
