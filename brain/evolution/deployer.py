"""Evolution Deployer — installs validated improvements into the brain.

Writes generated code to brain/evolution/evolved_*.py files.
Keeps backups for rollback. Logs all deployments.
"""

import shutil
import time
from pathlib import Path
from brain.config import EVOLVED_DIR, DATA_DIR


class EvolutionDeployer:
    """Deploys validated code improvements."""

    def __init__(self):
        EVOLVED_DIR.mkdir(parents=True, exist_ok=True)
        self.shortcuts_file = Path(__file__).parent / "evolved_shortcuts.py"
        self.backup_dir = DATA_DIR / "evolution_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def deploy_shortcuts(self, code: str) -> dict:
        """Deploy new shortcut handlers."""
        # Backup current version
        if self.shortcuts_file.exists():
            backup_name = f"shortcuts_{int(time.time())}.py.bak"
            shutil.copy2(self.shortcuts_file, self.backup_dir / backup_name)
            self._cleanup_backups()

        # Write new code
        try:
            self.shortcuts_file.write_text(code)
        except Exception as e:
            return {"deployed": False, "error": str(e), "timestamp": time.time()}

        return {
            "deployed": True,
            "file": str(self.shortcuts_file),
            "timestamp": time.time(),
        }

    def rollback_shortcuts(self) -> bool:
        """Rollback to the previous shortcuts version."""
        backups = sorted(self.backup_dir.glob("shortcuts_*.py.bak"))
        if not backups:
            return False

        latest = backups[-1]
        shutil.copy2(latest, self.shortcuts_file)
        latest.unlink()
        return True

    def _cleanup_backups(self, keep: int = 10):
        backups = sorted(self.backup_dir.glob("shortcuts_*.py.bak"))
        for old in backups[:-keep]:
            old.unlink()
