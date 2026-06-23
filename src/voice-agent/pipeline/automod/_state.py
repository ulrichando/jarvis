"""Shared constants + paths for the auto-mod loop (Spec B, Plane 3).

This module is import-safe (stdlib only) and has no side effects at
import time. The directory-existence checks happen lazily when callers
need to read/write.

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import os
from pathlib import Path


def _automod_home() -> Path:
    """Profile-scoped auto-mod dir. Honors JARVIS_HOME for tests."""
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis")
    return Path(home) / "auto-mods"


def queue_path() -> Path:
    return _automod_home() / "queue.jsonl"


def throttle_state_path() -> Path:
    return _automod_home() / "throttle.json"


def lockfile_path() -> Path:
    return _automod_home() / ".lock"


def pause_flag_path() -> Path:
    """Presence of this file pauses the autonomous evolution build cycle
    (universal-signal pattern, like ~/.jarvis/.silent-mode)."""
    return _automod_home() / ".evolution-paused"


def is_evolution_paused() -> bool:
    return pause_flag_path().exists()


def set_evolution_paused(paused: bool) -> bool:
    """Create/remove the pause flag. Returns the resulting paused state."""
    p = pause_flag_path()
    if paused:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("paused\n", encoding="utf-8")
    else:
        p.unlink(missing_ok=True)
    return paused


def artifact_path(automod_id: str) -> Path:
    return _automod_home() / f"{automod_id}.json"


def artifact_log_path(automod_id: str) -> Path:
    return _automod_home() / f"{automod_id}.log"


def intent_file_path(automod_id: str) -> Path:
    return _automod_home() / f"{automod_id}.intent.txt"


def evolution_log_path() -> Path:
    """Repurposed from the retired rule-evolution system. Spec B writes
    automod_* event records here; readers should filter out the legacy
    pytest-tmp pollution ('anchor_path' starts with '/tmp/pytest-')."""
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis")
    return Path(home) / "evolution_log.jsonl"


# Hard blocklist — paths the auto-mod loop MUST NEVER touch. Layered
# enforcement: spawner prompt mentions these, finalize.py refuses
# diffs touching them, merge CLI refuses to merge artifacts that touch
# them. The list itself is on the blocklist (no self-referential edit).
HARD_BLOCKLIST_PATHS = (
    "src/voice-agent/sanitizers/",
    "src/voice-agent/confab_detector.py",
    "src/voice-agent/pipeline/automod/",
    "src/voice-agent/evolution/",                  # human-owned fitness gate
    "src/voice-agent/pipeline/skill_review.py",  # reviewer prompt protected
    "src/voice-agent/prompts/soul.md",            # persona git-only
    "CLAUDE.md",
    ".claude/rules/regression-prevention.md",
    "MEMORY.md",
    "USER.md",
    # 2026-05-28: protect the auto-merge wrapper + CLI from being
    # modified by auto-mod itself. Even though ALLOWED_PATH_PREFIX
    # already blocks anything outside src/voice-agent/, listing these
    # explicitly here makes the intent legible to the next reader.
    "bin/jarvis-automod-impl",
    "bin/jarvis-automod",
    # 2026-06-21: the self-evolution SAFETY NET — the deploy watchdog, its
    # rollback, and the nightly trigger. Auto-mod must NEVER be able to edit or
    # disable the thing that rolls back a bad self-deploy. (deploy.py / watchdog.py
    # / selftest.py live under pipeline/automod/, already blocked above.)
    "bin/jarvis-evolution-watchdog",
    "bin/jarvis-evolution-nightly",
    "bin/jarvis-evolution-ondemand",
)

# Allowed prefix — diffs may touch only files under this prefix.
ALLOWED_PATH_PREFIX = "src/voice-agent/"


def is_blocked_path(path: str) -> bool:
    """True if `path` (repo-relative) is in the hard blocklist OR is
    outside the allowed prefix. Used by throttle, finalize, and merge."""
    p = path.strip().lstrip("./")
    for blocked in HARD_BLOCKLIST_PATHS:
        if p == blocked or p.startswith(blocked):
            return True
    if not p.startswith(ALLOWED_PATH_PREFIX):
        return True
    return False
