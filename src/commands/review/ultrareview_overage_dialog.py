"""Ultrareview overage dialog."""

from __future__ import annotations

from typing import Any


async def show_overage_dialog(**_kwargs: Any) -> bool:
    """Show the overage permission dialog when free reviews are exhausted."""
    return False
