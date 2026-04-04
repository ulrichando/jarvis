"""
Converted from types.ts
Business logic extracted from TypeScript/TSX source.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Callable, Awaitable

ModeState = type('ModeState', (), {})
AgentDefinition = type('AgentDefinition', (), {})
import re


@dataclass
class WithPreviousMode:
    previousMode: ModeState


@dataclass
class WithAgent:
    agent: AgentDefinition


@dataclass
class AgentValidationResult:
    isValid: bool
    warnings: list[str]
    errors: list[str]


AGENT_PATHS = {
    'FOLDER_NAME': '.claude',
    'AGENTS_DIR': 'agents',
}
