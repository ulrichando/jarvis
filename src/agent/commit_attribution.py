"""
Commit attribution system — tracks JARVIS's contribution percentage on file edits.

Monitors which files JARVIS edits vs. human edits (detected via mtime changes
not initiated by JARVIS), calculates contribution percentages, and generates
Co-Authored-By trailers for git commits.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model name sanitization
# ---------------------------------------------------------------------------

MODEL_PUBLIC_NAMES: dict[str, str] = {
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "llama3.3:70b": "Llama 3.3 70B",
    "qwen2.5:72b": "Qwen 2.5 72B",
    "deepseek-chat": "DeepSeek Chat",
    "gpt-4o": "GPT-4o",
}


def sanitize_model_name(model: str) -> str:
    """Map internal/raw model identifiers to clean public names."""
    for prefix, public in MODEL_PUBLIC_NAMES.items():
        if model.startswith(prefix):
            return public
    return model


# ---------------------------------------------------------------------------
# Per-file state
# ---------------------------------------------------------------------------

@dataclass
class FileEditState:
    """Tracks edit statistics for a single file."""

    path: str
    original_content: str = ""
    original_hash: str = ""
    original_mtime: float = 0.0
    jarvis_edits: int = 0
    human_edits: int = 0
    jarvis_lines_added: int = 0
    jarvis_lines_removed: int = 0

    # Internal: mtime right after the last JARVIS edit so we can distinguish
    # human edits from our own.
    _last_jarvis_mtime: float = field(default=0.0, repr=False)


# ---------------------------------------------------------------------------
# Attribution tracker
# ---------------------------------------------------------------------------

class AttributionTracker:
    """Session-scoped tracker for JARVIS vs. human file contributions."""

    def __init__(self) -> None:
        self._file_states: dict[str, FileEditState] = {}
        self._user_prompts: int = 0
        self._permission_prompts: int = 0
        self._model_name: str = ""

    # -- configuration ------------------------------------------------------

    def set_model(self, model: str) -> None:
        """Set the current model name (raw provider id is fine)."""
        self._model_name = model

    # -- counters -----------------------------------------------------------

    def record_user_prompt(self) -> None:
        self._user_prompts += 1

    def record_permission_prompt(self) -> None:
        self._permission_prompts += 1

    # -- file tracking ------------------------------------------------------

    def track_file(self, path: str) -> None:
        """Start tracking *path*. Snapshots current content + mtime."""
        path = os.path.abspath(path)
        if path in self._file_states:
            return  # already tracked

        content = ""
        content_hash = ""
        mtime = 0.0

        try:
            with open(path, "r", errors="replace") as f:
                content = f.read()
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            # File doesn't exist yet — that's fine, it will be created.
            pass
        except OSError as exc:
            log.debug("Could not snapshot %s: %s", path, exc)

        self._file_states[path] = FileEditState(
            path=path,
            original_content=content,
            original_hash=content_hash,
            original_mtime=mtime,
            _last_jarvis_mtime=mtime,
        )

    def record_edit(
        self,
        path: str,
        lines_added: int = 0,
        lines_removed: int = 0,
    ) -> None:
        """Record that JARVIS made an edit to *path*."""
        path = os.path.abspath(path)

        # Auto-track if not already tracked.
        if path not in self._file_states:
            self.track_file(path)

        state = self._file_states[path]
        state.jarvis_edits += 1
        state.jarvis_lines_added += lines_added
        state.jarvis_lines_removed += lines_removed

        # Update last-known mtime so detect_human_edit won't misfire.
        try:
            state._last_jarvis_mtime = os.path.getmtime(path)
        except OSError:
            pass

    def detect_human_edit(self, path: str) -> bool:
        """Check if *path* was modified externally since our last touch.

        Returns True (and increments human_edits) when the file's mtime
        changed without a corresponding ``record_edit`` call.
        """
        path = os.path.abspath(path)
        state = self._file_states.get(path)
        if state is None:
            return False

        try:
            current_mtime = os.path.getmtime(path)
        except OSError:
            return False

        if current_mtime > state._last_jarvis_mtime:
            state.human_edits += 1
            state._last_jarvis_mtime = current_mtime
            return True

        return False

    # -- contribution stats -------------------------------------------------

    def get_contribution_pct(self, path: str) -> float:
        """Return JARVIS's contribution percentage for a single file.

        The metric is based on lines changed (added + removed).  If the
        human's changes can't be measured in lines we fall back to edit
        counts.
        """
        path = os.path.abspath(path)
        state = self._file_states.get(path)
        if state is None:
            return 0.0

        jarvis_lines = state.jarvis_lines_added + state.jarvis_lines_removed

        # We don't have fine-grained line counts for human edits; estimate
        # them from the edit-count ratio when no line data is available.
        if jarvis_lines == 0 and state.jarvis_edits == 0:
            return 0.0

        if state.human_edits == 0:
            return 100.0

        if jarvis_lines > 0:
            # Estimate human lines proportionally from edit counts.
            avg_jarvis_lines = jarvis_lines / max(state.jarvis_edits, 1)
            estimated_human_lines = state.human_edits * avg_jarvis_lines
            total = jarvis_lines + estimated_human_lines
            if total == 0:
                return 0.0
            return round(jarvis_lines / total * 100, 1)

        # No line data at all — fall back to pure edit counts.
        total_edits = state.jarvis_edits + state.human_edits
        if total_edits == 0:
            return 0.0
        return round(state.jarvis_edits / total_edits * 100, 1)

    def get_overall_contribution(self) -> float:
        """Average JARVIS contribution across all tracked files."""
        if not self._file_states:
            return 0.0

        pcts = [self.get_contribution_pct(p) for p in self._file_states]
        return round(sum(pcts) / len(pcts), 1)

    # -- git trailer --------------------------------------------------------

    def get_co_author_trailer(self) -> str:
        """Return a ``Co-Authored-By`` line suitable for git commit trailers."""
        public_name = sanitize_model_name(self._model_name) if self._model_name else "JARVIS"
        hostname = socket.gethostname()
        return f"Co-Authored-By: JARVIS ({public_name}) <jarvis@{hostname}>"

    # -- human-readable summary ---------------------------------------------

    def get_summary(self) -> str:
        """Return a short textual summary of attribution stats."""
        n_files = len(self._file_states)
        contribution = self.get_overall_contribution()
        lines = [
            f"Files modified: {n_files}",
            f"JARVIS contribution: {contribution}%",
            f"User prompts: {self._user_prompts}, permission approvals: {self._permission_prompts}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tracker: AttributionTracker | None = None


def get_attribution_tracker() -> AttributionTracker:
    """Return (and lazily create) the session-wide AttributionTracker."""
    global _tracker
    if _tracker is None:
        _tracker = AttributionTracker()
    return _tracker
