"""JARVIS Migration Manager — versioned schema/data migrations."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Migration:
    version: str
    description: str
    up: Callable
    down: Callable | None = None


class MigrationManager:
    """Register, track, and apply ordered migrations."""

    def __init__(self, db_dir: str):
        self._state_file = Path(db_dir) / "migrations.json"
        self._migrations: list[Migration] = []
        self._applied: list[str] = []
        self._load_state()

    def _load_state(self) -> None:
        if self._state_file.exists():
            data = json.loads(self._state_file.read_text())
            self._applied = data.get("applied", [])

    def _save_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps({"applied": self._applied}))

    def register(self, version: str, description: str, up: Callable, down: Callable | None = None) -> None:
        if any(m.version == version for m in self._migrations):
            raise ValueError(f"Migration {version} already registered")
        self._migrations.append(Migration(version=version, description=description, up=up, down=down))

    def pending(self) -> list[Migration]:
        return [m for m in self._migrations if m.version not in self._applied]

    def current_version(self) -> str:
        return self._applied[-1] if self._applied else "0.0"

    def apply_all(self) -> list[str]:
        applied = []
        for m in self.pending():
            m.up()
            self._applied.append(m.version)
            applied.append(m.version)
        self._save_state()
        return applied

    def rollback(self, to_version: str) -> None:
        while self._applied and self._applied[-1] != to_version:
            ver = self._applied[-1]
            mig = next((m for m in self._migrations if m.version == ver), None)
            if mig and mig.down:
                mig.down()
            self._applied.pop()
        self._save_state()


def get_migration_manager(db_dir: str) -> MigrationManager:
    """Return a MigrationManager pre-loaded with built-in migrations."""
    mgr = MigrationManager(db_dir)
    mgr.register("1.0", "Initial schema", lambda: None)
    mgr.register("2.0", "Add memory lattice tables", lambda: None)
    return mgr
