"""
Converted from EffortIndicator.ts
Business logic extracted from TypeScript/TSX source.
"""

from typing import Optional, Any, Literal, Callable, Awaitable

EffortValue = type('EffortValue', (), {})
EffortLevel = type('EffortLevel', (), {})
import re


def getEffortNotificationText(effortValue: Optional[EffortValue], model: str) -> Optional[str]:
    if not modelSupportsEffort(model):
        return None
    level = getDisplayedEffortLevel(model, effortValue)
    return f"{effortLevelToSymbol(level)} {level} · /effort"


def effortLevelToSymbol(level: EffortLevel) -> str:
    match level:
        case 'low':
            return EFFORT_LOW
        case 'medium':
            return EFFORT_MEDIUM
        case 'high':
            return EFFORT_HIGH
        case 'max':
            return EFFORT_MAX
        case _:
            # Defensive: level can originate from remote config. If an unknown
            # value slips through, render the high symbol rather than undefined.
            return EFFORT_HIGH
