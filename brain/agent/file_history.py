"""JARVIS File History — pre-edit snapshots and version tracking."""

import os
import hashlib
import time
import shutil
import logging
from dataclasses import dataclass, field
from pathlib import Path
from brain.config import JARVIS_HOME

log = logging.getLogger(__name__)

MAX_SNAPSHOTS = 100  # Per file
HISTORY_DIR = JARVIS_HOME / "file_history"


@dataclass
class FileSnapshot:
    """A snapshot of a file at a point in time."""
    path: str           # Original file path
    version: int        # Version number (1 = before first edit)
    timestamp: float
    content_hash: str   # SHA-256 of content
    backup_path: str    # Where the backup is stored
    message_id: str = ""  # Linked to conversation message
    size: int = 0


class FileHistory:
    """Tracks file versions across edits."""

    def __init__(self):
        self._snapshots: dict[str, list[FileSnapshot]] = {}  # path -> sorted snapshots
        self._sequence: int = 0
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    def snapshot_before_edit(self, path: str, message_id: str = "") -> FileSnapshot | None:
        """Create a snapshot of a file before it's modified.
        Returns the snapshot, or None if file doesn't exist."""
        path = os.path.realpath(os.path.expanduser(path))
        if not os.path.exists(path) or os.path.isdir(path):
            return None

        try:
            content = open(path, 'rb').read()
        except Exception:
            return None

        content_hash = hashlib.sha256(content).hexdigest()

        # Check if we already have this exact version
        existing = self._snapshots.get(path, [])
        if existing and existing[-1].content_hash == content_hash:
            return existing[-1]  # No change since last snapshot

        # Create backup
        self._sequence += 1
        path_hash = hashlib.sha256(path.encode()).hexdigest()[:16]
        version = len(existing) + 1
        backup_name = f"{path_hash}@v{version}.backup"
        backup_path = str(HISTORY_DIR / backup_name)

        try:
            shutil.copy2(path, backup_path)
        except Exception as e:
            log.warning("Failed to snapshot %s: %s", path, e)
            return None

        snap = FileSnapshot(
            path=path, version=version, timestamp=time.time(),
            content_hash=content_hash, backup_path=backup_path,
            message_id=message_id, size=len(content),
        )

        if path not in self._snapshots:
            self._snapshots[path] = []
        self._snapshots[path].append(snap)

        # Evict old snapshots
        if len(self._snapshots[path]) > MAX_SNAPSHOTS:
            old = self._snapshots[path].pop(0)
            try:
                os.unlink(old.backup_path)
            except OSError:
                pass

        return snap

    def get_snapshots(self, path: str) -> list[FileSnapshot]:
        """Get all snapshots for a file."""
        path = os.path.realpath(os.path.expanduser(path))
        return self._snapshots.get(path, [])

    def get_latest_snapshot(self, path: str) -> FileSnapshot | None:
        """Get the most recent snapshot for a file."""
        snaps = self.get_snapshots(path)
        return snaps[-1] if snaps else None

    def restore_snapshot(self, snapshot: FileSnapshot) -> bool:
        """Restore a file from a snapshot."""
        if not os.path.exists(snapshot.backup_path):
            return False
        try:
            shutil.copy2(snapshot.backup_path, snapshot.path)
            return True
        except Exception:
            return False

    def get_diff_stats(self, path: str) -> dict:
        """Get insertion/deletion stats between first and current version."""
        path = os.path.realpath(os.path.expanduser(path))
        snaps = self._snapshots.get(path, [])
        if not snaps or not os.path.exists(path):
            return {"insertions": 0, "deletions": 0}

        try:
            original = open(snaps[0].backup_path, 'r', errors='replace').readlines()
            current = open(path, 'r', errors='replace').readlines()
        except Exception:
            return {"insertions": 0, "deletions": 0}

        import difflib
        diff = list(difflib.unified_diff(original, current, lineterm=''))
        insertions = sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))

        return {"insertions": insertions, "deletions": deletions}

    def get_all_modified_files(self) -> list[str]:
        """Get all files that have been snapshotted."""
        return list(self._snapshots.keys())

    def cleanup(self):
        """Remove all backup files."""
        for snaps in self._snapshots.values():
            for snap in snaps:
                try:
                    os.unlink(snap.backup_path)
                except OSError:
                    pass
        self._snapshots.clear()


# Module singleton
_history: FileHistory | None = None

def get_file_history() -> FileHistory:
    global _history
    if _history is None:
        _history = FileHistory()
    return _history
