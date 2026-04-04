"""
Converted from useStalledAnimation.ts
Business logic extracted from TypeScript/TSX source.
"""

from typing import Optional, Any, Literal, Callable, Awaitable
import re


def useStalledAnimation(time: int | float, currentResponseLength: int | float, hasActiveTools=False, reducedMotion=False) -> Any:
    isStalled: boolean
    stalledIntensity: number
