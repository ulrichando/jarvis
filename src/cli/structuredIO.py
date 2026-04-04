"""Structured I/O for SDK/daemon mode communication."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional

from .ndjsonSafeStringify import ndjson_safe_stringify


class StructuredIO:
    """Handles NDJSON-based structured I/O for SDK communication."""

    def __init__(
        self,
        write: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._write = write or (lambda s: sys.stdout.write(s))

    def emit(self, event: dict[str, Any]) -> None:
        """Emit a structured event as NDJSON."""
        self._write(ndjson_safe_stringify(event) + "\n")

    def emit_result(self, result: str, is_error: bool = False) -> None:
        """Emit a result event."""
        self.emit({
            "type": "result",
            "subtype": "error" if is_error else "success",
            "result": result,
            "is_error": is_error,
        })

    def emit_progress(self, text: str) -> None:
        """Emit a progress event."""
        self.emit({
            "type": "assistant",
            "message": {"role": "assistant", "content": text},
        })
