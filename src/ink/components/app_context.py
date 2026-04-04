"""Application context for Ink."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class AppContext:
    """Application-level context passed down to components."""
    exit: Callable[[Exception | None], None] | None = None
