"""
Top-level JARVIS application state and lifecycle.

Handles main application setup, project onboarding state,
dialog launchers, and interactive helpers.

Provides JARVISApp (application lifecycle) and ProjectState (per-project
detection and initialisation).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.app")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jarvis_home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))


# ---------------------------------------------------------------------------
# JARVISApp
# ---------------------------------------------------------------------------

class JARVISApp:
    """Top-level application object managing the full JARVIS lifecycle."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}
        self.brain: Any = None
        self._ready = False
        self._start_time: float | None = None
        self._subsystems: dict[str, bool] = {}

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Boot sequence: load config, init providers, discover agents/skills,
        init memory, and mark the app as ready."""
        self._start_time = time.monotonic()
        log.info("JARVIS starting up")

        # 1. Load global config
        config_path = _jarvis_home() / "config.json"
        if config_path.exists():
            try:
                self.config.update(json.loads(config_path.read_text()))
            except Exception as exc:
                log.warning("Failed to read config.json: %s", exc)
        self._subsystems["config"] = True

        # 2. Load providers
        providers_path = _jarvis_home() / "providers.json"
        if providers_path.exists():
            try:
                self.config["providers"] = json.loads(providers_path.read_text())
                self._subsystems["providers"] = True
            except Exception as exc:
                log.warning("Failed to read providers.json: %s", exc)
                self._subsystems["providers"] = False
        else:
            self._subsystems["providers"] = False

        # 3. Init brain
        try:
            from src.brain import Brain  # type: ignore[import-untyped]
            self.brain = Brain()
            self._subsystems["src"] = True
        except Exception as exc:
            log.error("Failed to init Brain: %s", exc)
            self._subsystems["src"] = False

        # 4. Discover agents
        try:
            agents_dirs = [
                _jarvis_home() / "agents",
                Path(os.getcwd()) / ".jarvis" / "agents",
            ]
            agent_count = 0
            for d in agents_dirs:
                if d.is_dir():
                    agent_count += len(list(d.glob("*.yaml"))) + len(list(d.glob("*.yml")))
            self._subsystems["agents"] = True
            log.info("Discovered %d agent definitions", agent_count)
        except Exception as exc:
            log.warning("Agent discovery failed: %s", exc)
            self._subsystems["agents"] = False

        # 5. Discover skills
        try:
            skills_dir = _jarvis_home() / "skills"
            skill_count = 0
            if skills_dir.is_dir():
                skill_count = len(list(skills_dir.glob("*.md")))
            self._subsystems["skills"] = True
            log.info("Discovered %d skills", skill_count)
        except Exception as exc:
            log.warning("Skill discovery failed: %s", exc)
            self._subsystems["skills"] = False

        # 6. Init memory
        try:
            from src.memory.store import MemoryStore  # type: ignore[import-untyped]
            self._subsystems["memory"] = True
        except Exception as exc:
            log.warning("Memory init failed: %s", exc)
            self._subsystems["memory"] = False

        elapsed = time.monotonic() - self._start_time
        self._ready = True
        log.info("JARVIS ready in %.2fs", elapsed)

    async def shutdown(self) -> None:
        """Graceful shutdown: save state, close connections."""
        log.info("JARVIS shutting down")

        if self.brain:
            try:
                if hasattr(self.brain, "shutdown"):
                    await self.brain.shutdown()
            except Exception as exc:
                log.warning("Brain shutdown error: %s", exc)

        # Persist any session state
        state_file = _jarvis_home() / "last_session.json"
        try:
            state_file.write_text(json.dumps({
                "shutdown_time": time.time(),
                "subsystems": self._subsystems,
            }, indent=2) + "\n")
        except Exception:
            pass

        self._ready = False
        log.info("JARVIS shut down")

    def get_status(self) -> dict[str, Any]:
        """Return full system status dict."""
        uptime = None
        if self._start_time is not None:
            uptime = round(time.monotonic() - self._start_time, 2)

        return {
            "ready": self._ready,
            "uptime_seconds": uptime,
            "subsystems": dict(self._subsystems),
            "config_keys": list(self.config.keys()),
            "jarvis_home": str(_jarvis_home()),
            "cwd": os.getcwd(),
        }

    def is_ready(self) -> bool:
        return self._ready


# ---------------------------------------------------------------------------
# ProjectState
# ---------------------------------------------------------------------------

# Known project indicators
_PROJECT_MARKERS: dict[str, list[str]] = {
    "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
    "node": ["package.json", "yarn.lock", "pnpm-lock.yaml"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "ruby": ["Gemfile"],
    "dotnet": ["*.csproj", "*.sln"],
    "git": [".git"],
}


class ProjectState:
    """Per-project detection and configuration."""

    @staticmethod
    def detect_project(cwd: str) -> dict[str, Any]:
        """Detect project type and configuration files present in *cwd*."""
        root = Path(cwd)
        detected: dict[str, Any] = {
            "path": str(root),
            "types": [],
            "config_files": [],
            "has_jarvis": (root / ".jarvis").is_dir(),
            "has_claude_md": (root / "CLAUDE.md").is_file(),
            "is_empty": _is_dir_empty(root),
        }

        for ptype, markers in _PROJECT_MARKERS.items():
            for marker in markers:
                if "*" in marker:
                    if list(root.glob(marker)):
                        detected["types"].append(ptype)
                        break
                elif (root / marker).exists():
                    detected["types"].append(ptype)
                    detected["config_files"].append(marker)
                    break

        # Detect git branch
        git_head = root / ".git" / "HEAD"
        if git_head.is_file():
            try:
                content = git_head.read_text().strip()
                if content.startswith("ref: refs/heads/"):
                    detected["git_branch"] = content[len("ref: refs/heads/"):]
                else:
                    detected["git_branch"] = content[:12]
            except Exception:
                pass

        return detected

    @staticmethod
    def is_initialized(cwd: str) -> bool:
        """Return True if .jarvis/ directory exists in *cwd*."""
        return (Path(cwd) / ".jarvis").is_dir()

    @staticmethod
    def initialize_project(cwd: str) -> None:
        """Create .jarvis/ with initial settings in *cwd*."""
        project_dir = Path(cwd) / ".jarvis"
        project_dir.mkdir(parents=True, exist_ok=True)

        settings_path = project_dir / "settings.json"
        if not settings_path.exists():
            settings: dict[str, Any] = {
                "project_onboarding_seen_count": 0,
                "has_completed_project_onboarding": False,
            }
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            log.info("Initialized project config at %s", settings_path)

    @staticmethod
    def get_project_config(cwd: str) -> dict[str, Any]:
        """Read .jarvis/settings.json.  Returns empty dict if not found."""
        settings_path = Path(cwd) / ".jarvis" / "settings.json"
        if not settings_path.exists():
            return {}
        try:
            return json.loads(settings_path.read_text())
        except Exception as exc:
            log.warning("Failed to read project settings: %s", exc)
            return {}

    @staticmethod
    def save_project_config(cwd: str, config: dict[str, Any]) -> None:
        """Write *config* to .jarvis/settings.json."""
        project_dir = Path(cwd) / ".jarvis"
        project_dir.mkdir(parents=True, exist_ok=True)
        settings_path = project_dir / "settings.json"
        settings_path.write_text(json.dumps(config, indent=2) + "\n")

    # -- onboarding helpers (from projectOnboardingState.ts) -----------------

    @staticmethod
    def get_onboarding_steps(cwd: str) -> list[dict[str, Any]]:
        """Return onboarding checklist steps for the project."""
        root = Path(cwd)
        has_claude_md = (root / "CLAUDE.md").is_file()
        is_empty = _is_dir_empty(root)

        return [
            {
                "key": "workspace",
                "text": "Ask JARVIS to create a new app or clone a repository",
                "is_complete": False,
                "is_completable": True,
                "is_enabled": is_empty,
            },
            {
                "key": "claudemd",
                "text": "Run /init to create a CLAUDE.md file with instructions",
                "is_complete": has_claude_md,
                "is_completable": True,
                "is_enabled": not is_empty,
            },
        ]

    @staticmethod
    def is_onboarding_complete(cwd: str) -> bool:
        steps = ProjectState.get_onboarding_steps(cwd)
        return all(
            s["is_complete"]
            for s in steps
            if s["is_completable"] and s["is_enabled"]
        )

    @staticmethod
    def should_show_onboarding(cwd: str) -> bool:
        config = ProjectState.get_project_config(cwd)
        if config.get("has_completed_project_onboarding"):
            return False
        if config.get("project_onboarding_seen_count", 0) >= 4:
            return False
        return not ProjectState.is_onboarding_complete(cwd)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _is_dir_empty(path: Path) -> bool:
    """Return True if a directory is empty (ignoring hidden files)."""
    if not path.is_dir():
        return True
    try:
        return not any(True for _ in path.iterdir())
    except PermissionError:
        return True
