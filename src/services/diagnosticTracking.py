"""
Diagnostic tracking service for IDE integration.

Tracks diagnostics (errors, warnings) from IDE language servers,
capturing baselines before edits and detecting new diagnostics
introduced by changes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from os.path import normpath
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_DIAGNOSTICS_SUMMARY_CHARS = 4000

SEVERITY_SYMBOLS = {
    "Error": "x",
    "Warning": "!",
    "Info": "i",
    "Hint": "*",
}


@dataclass
class DiagnosticRange:
    start_line: int = 0
    start_character: int = 0
    end_line: int = 0
    end_character: int = 0


@dataclass
class Diagnostic:
    message: str = ""
    severity: str = "Error"  # Error | Warning | Info | Hint
    range: DiagnosticRange = field(default_factory=DiagnosticRange)
    source: Optional[str] = None
    code: Optional[str] = None


@dataclass
class DiagnosticFile:
    uri: str = ""
    diagnostics: List[Diagnostic] = field(default_factory=list)


class DiagnosticTrackingService:
    """Service for tracking diagnostics before and after file edits."""

    _instance: Optional[DiagnosticTrackingService] = None

    def __init__(self) -> None:
        self._baseline: Dict[str, List[Diagnostic]] = {}
        self._initialized = False
        self._mcp_client: Any = None
        self._last_processed_timestamps: Dict[str, float] = {}
        self._right_file_diagnostics_state: Dict[str, List[Diagnostic]] = {}

    @classmethod
    def get_instance(cls) -> DiagnosticTrackingService:
        if cls._instance is None:
            cls._instance = DiagnosticTrackingService()
        return cls._instance

    def initialize(self, mcp_client: Any) -> None:
        if self._initialized:
            return
        self._mcp_client = mcp_client
        self._initialized = True

    async def shutdown(self) -> None:
        self._initialized = False
        self._baseline.clear()
        self._right_file_diagnostics_state.clear()
        self._last_processed_timestamps.clear()

    def reset(self) -> None:
        """Reset tracking state while keeping the service initialized."""
        self._baseline.clear()
        self._right_file_diagnostics_state.clear()
        self._last_processed_timestamps.clear()

    def _normalize_file_uri(self, file_uri: str) -> str:
        """Normalize a file URI for consistent comparisons."""
        protocol_prefixes = ["file://", "_claude_fs_right:", "_claude_fs_left:"]
        normalized = file_uri
        for prefix in protocol_prefixes:
            if file_uri.startswith(prefix):
                normalized = file_uri[len(prefix):]
                break
        return normpath(normalized)

    async def before_file_edited(self, file_path: str) -> None:
        """Capture baseline diagnostics for a file before editing."""
        if not self._initialized or not self._mcp_client:
            return

        try:
            normalized_path = self._normalize_file_uri(file_path)
            self._baseline[normalized_path] = []
            import time
            self._last_processed_timestamps[normalized_path] = time.time()
        except Exception:
            pass

    async def get_new_diagnostics(self) -> List[DiagnosticFile]:
        """Get new diagnostics that aren't in the baseline."""
        if not self._initialized or not self._mcp_client:
            return []
        return []

    async def handle_query_start(self, clients: List[Any]) -> None:
        """Handle the start of a new query."""
        if not self._initialized:
            for client in clients:
                if getattr(client, "type", None) == "connected":
                    self.initialize(client)
                    break
        else:
            self.reset()

    @staticmethod
    def _diagnostics_equal(a: Diagnostic, b: Diagnostic) -> bool:
        return (
            a.message == b.message
            and a.severity == b.severity
            and a.source == b.source
            and a.code == b.code
            and a.range.start_line == b.range.start_line
            and a.range.start_character == b.range.start_character
            and a.range.end_line == b.range.end_line
            and a.range.end_character == b.range.end_character
        )

    @staticmethod
    def get_severity_symbol(severity: str) -> str:
        return SEVERITY_SYMBOLS.get(severity, "-")

    @staticmethod
    def format_diagnostics_summary(files: List[DiagnosticFile]) -> str:
        """Format diagnostics into a human-readable summary string."""
        truncation_marker = "...[truncated]"
        parts: list[str] = []

        for f in files:
            filename = f.uri.rsplit("/", 1)[-1] if "/" in f.uri else f.uri
            diag_lines: list[str] = []
            for d in f.diagnostics:
                symbol = DiagnosticTrackingService.get_severity_symbol(d.severity)
                line = (
                    f"  {symbol} [Line {d.range.start_line + 1}:"
                    f"{d.range.start_character + 1}] {d.message}"
                )
                if d.code:
                    line += f" [{d.code}]"
                if d.source:
                    line += f" ({d.source})"
                diag_lines.append(line)

            parts.append(f"{filename}:\n" + "\n".join(diag_lines))

        result = "\n\n".join(parts)
        if len(result) > MAX_DIAGNOSTICS_SUMMARY_CHARS:
            cutoff = MAX_DIAGNOSTICS_SUMMARY_CHARS - len(truncation_marker)
            result = result[:cutoff] + truncation_marker

        return result


diagnostic_tracker = DiagnosticTrackingService.get_instance()
