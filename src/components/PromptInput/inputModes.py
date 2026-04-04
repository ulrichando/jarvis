"""
Converted from inputModes.ts
Business logic extracted from TypeScript/TSX source.
"""

from typing import Any, Literal, Callable, Awaitable

PromptInputMode = type('PromptInputMode', (), {})
HistoryMode = type('HistoryMode', (), {})
import re


def prependModeCharacterToInput(input: str, mode: PromptInputMode) -> str:
    match mode:
        case 'bash':
            return f"!{input}"
        case _:
            return input


def getModeFromInput(input: str) -> HistoryMode:
    if input.startsWith('!'):
        return 'bash'
    return 'prompt'


def getValueFromInput(input: str) -> str:
    mode = getModeFromInput(input)
    if mode == 'prompt':
        return input
    return input[1:]


def isInputModeCharacter(input: str) -> bool:
    return input == '!'
