"""
Converted from RejectedPlanMessage.tsx
Business logic extracted from TypeScript/TSX source.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Callable, Awaitable
import re


@dataclass
class Props:
    plan: str
