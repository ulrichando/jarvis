"""
Converted from ChannelDowngradeDialog.tsx
Business logic extracted from TypeScript/TSX source.
"""

from dataclasses import dataclass, field
from typing import Optional, Any, Literal, Callable, Awaitable
import re


@dataclass
class Props:
    currentVersion: str
    onChoice: lambda choice: ChannelDowngradeChoice


def handleSelect(value):
    onChoice(value)


def handleCancel():
    onChoice("cancel")
