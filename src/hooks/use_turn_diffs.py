"""Track file diffs per conversation turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TurnFileDiff:
    file_path: str
    hunks: List[Any] = field(default_factory=list)
    is_new_file: bool = False
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class TurnDiff:
    turn_index: int
    user_prompt_preview: str
    timestamp: str
    files: Dict[str, TurnFileDiff] = field(default_factory=dict)
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


class TurnDiffTracker:
    """Tracks file diffs per conversation turn.

    Equivalent to useTurnDiffs React hook.
    """

    def __init__(self):
        self._completed_turns: List[TurnDiff] = []
        self._current_turn: Optional[TurnDiff] = None

    @property
    def turns(self) -> List[TurnDiff]:
        return list(self._completed_turns)

    def start_turn(self, turn_index: int, prompt: str, timestamp: str) -> None:
        self._current_turn = TurnDiff(
            turn_index=turn_index,
            user_prompt_preview=prompt[:100],
            timestamp=timestamp,
        )

    def add_file_diff(self, file_path: str, diff: TurnFileDiff) -> None:
        if self._current_turn:
            self._current_turn.files[file_path] = diff

    def end_turn(self) -> None:
        if self._current_turn:
            turn = self._current_turn
            turn.files_changed = len(turn.files)
            turn.lines_added = sum(f.lines_added for f in turn.files.values())
            turn.lines_removed = sum(f.lines_removed for f in turn.files.values())
            self._completed_turns.append(turn)
            self._current_turn = None
