"""
Converted from SentryErrorBoundary.ts
Business logic extracted from TypeScript/TSX source.
"""

from dataclasses import dataclass, field
from typing import Optional, Any, Literal, Callable, Awaitable


@dataclass
class Props:
    children: Any


@dataclass
class State:
    hasError: bool
