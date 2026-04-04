"""
Converted from utils.ts
Business logic extracted from TypeScript/TSX source.
"""

from typing import Any, Literal, Callable, Awaitable

SettingSource = type('SettingSource', (), {})
import re


def getAgentSourceDisplayName(source: SettingSource | Literal['all'] | Literal['built-in'] | Literal['plugin']) -> str:
    if source == 'all':
        return 'Agents'
    if source == 'built-in':
        return 'Built-in agents'
    if source == 'plugin':
        return 'Plugin agents'
    return capitalize(getSettingSourceName(source))
