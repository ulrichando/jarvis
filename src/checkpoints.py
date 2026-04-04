"""JARVIS Checkpoint System — snapshot files before editing for real undo.

Inspired by automatic checkpoint system:
- Every file edit creates a snapshot of the previous contents
- /undo reverts to the last checkpoint
- Checkpoints stored in ~/.jarvis/checkpoints/ with timestamps
- Automatic cleanup of old checkpoints (keep last 50)
"""

import json
import time
import shutil
from pathlib import Path
from dataclasses import dataclass
from src.config import JARVIS_HOME


CHECKPOINT_DIR = JARVIS_HOME / "checkpoints"
MAX_CHECKPOINTS = 50


@dataclass
class Checkpoint:
    """A single file checkpoint."""
    file_path: str
    content: str
    timestamp: float
    tool_name: str  # Which tool created this (write_file, edit_file, bash)

    @property
    def age_human(self) -> str:
        age = time.time() - self.timestamp
        if age < 60:
            return f"{int(age)}s ago"
        elif age < 3600:
            return f"{int(age/60)}m ago"
        else:
            return f"{int(age/3600)}h ago"


class CheckpointManager:
    """Manages file checkpoints for undo capability."""

    def __init__(self):
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        self._checkpoints: list[Checkpoint] = []
        self._index_path = CHECKPOINT_DIR / "index.json"
        self._load_index()

    def _load_index(self):
        """Load checkpoint index from disk."""
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text())
                self._checkpoints = [
                    Checkpoint(**cp) for cp in data
                    if (CHECKPOINT_DIR / f"{cp['timestamp']}.snap").exists()
                ]
            except Exception:
                self._checkpoints = []

    def _save_index(self):
        """Persist checkpoint index."""
        data = [
            {
                "file_path": cp.file_path,
                "content": "",  # Don't store content in index
                "timestamp": cp.timestamp,
                "tool_name": cp.tool_name,
            }
            for cp in self._checkpoints
        ]
        self._index_path.write_text(json.dumps(data, indent=2))

    def snapshot(self, file_path: str, tool_name: str = "edit"):
        """Take a snapshot of a file before it's modified.

        Call this BEFORE write_file or edit_file executes.
        """
        path = Path(file_path).expanduser().resolve()
        if not path.exists() or path.is_dir():
            return

        try:
            content = path.read_text()
        except Exception:
            return

        ts = time.time()
        # Save content to snapshot file
        snap_path = CHECKPOINT_DIR / f"{ts}.snap"
        snap_path.write_text(content)

        cp = Checkpoint(
            file_path=str(path),
            content="",  # Stored on disk, not in memory
            timestamp=ts,
            tool_name=tool_name,
        )
        self._checkpoints.append(cp)
        self._save_index()

        # Cleanup old checkpoints
        self._cleanup()

    def undo(self) -> dict:
        """Revert the most recent file edit.

        Returns dict with status info.
        """
        if not self._checkpoints:
            return {"success": False, "message": "No checkpoints to undo."}

        cp = self._checkpoints.pop()
        snap_path = CHECKPOINT_DIR / f"{cp.timestamp}.snap"

        if not snap_path.exists():
            self._save_index()
            return {"success": False, "message": "Checkpoint file missing."}

        content = snap_path.read_text()
        target = Path(cp.file_path)

        # Save current version as a "redo" point
        if target.exists():
            redo_path = CHECKPOINT_DIR / f"{cp.timestamp}.redo"
            redo_path.write_text(target.read_text())

        try:
            target.write_text(content)
            # Cleanup the snapshot file
            snap_path.unlink(missing_ok=True)
            self._save_index()
            return {
                "success": True,
                "file": cp.file_path,
                "tool": cp.tool_name,
                "age": cp.age_human,
                "message": f"Reverted {cp.file_path} ({cp.tool_name}, {cp.age_human})",
            }
        except Exception as e:
            self._save_index()
            return {"success": False, "message": f"Failed to restore: {e}"}

    def list_checkpoints(self, limit: int = 10) -> list[dict]:
        """List recent checkpoints."""
        return [
            {
                "file": cp.file_path,
                "tool": cp.tool_name,
                "age": cp.age_human,
                "timestamp": cp.timestamp,
            }
            for cp in reversed(self._checkpoints[-limit:])
        ]

    def _cleanup(self):
        """Remove old checkpoints beyond MAX_CHECKPOINTS."""
        while len(self._checkpoints) > MAX_CHECKPOINTS:
            old = self._checkpoints.pop(0)
            snap_path = CHECKPOINT_DIR / f"{old.timestamp}.snap"
            snap_path.unlink(missing_ok=True)
        self._save_index()

    @property
    def count(self) -> int:
        return len(self._checkpoints)
