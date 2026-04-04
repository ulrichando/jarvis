"""Schemas for LSP tool input/output."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LSPInput:
    action: str  # "diagnostics", "definition", "references", "hover", "completion"
    file_path: str
    line: Optional[int] = None
    character: Optional[int] = None
    symbol: Optional[str] = None


@dataclass
class LSPOutput:
    success: bool
    result: Any = None
    error: Optional[str] = None
