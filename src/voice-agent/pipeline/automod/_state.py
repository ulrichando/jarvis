"""Shared constants + paths for the auto-mod loop (Spec B, Plane 3).

This module is import-safe (stdlib only) and has no side effects at
import time. The directory-existence checks happen lazily when callers
need to read/write.

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import os
import posixpath
from pathlib import Path


def _automod_home() -> Path:
    """Profile-scoped auto-mod dir. Honors JARVIS_HOME for tests."""
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis")
    return Path(home) / "auto-mods"


def queue_path() -> Path:
    return _automod_home() / "queue.jsonl"


def throttle_state_path() -> Path:
    return _automod_home() / "throttle.json"


def cost_ledger_path() -> Path:
    return _automod_home() / "cost-ledger.json"


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


def auto_flag_path() -> Path:
    """Presence enables AUTO mode: the cycle timer runs self-assessment →
    queue → build → review automatically. Absent = MANUAL (user drives via
    Build it / Run cycle)."""
    return _automod_home() / ".evolution-auto"


def is_auto_mode() -> bool:
    return auto_flag_path().exists()


def set_auto_mode(on: bool) -> bool:
    p = auto_flag_path()
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("auto\n", encoding="utf-8")
    else:
        p.unlink(missing_ok=True)
    return on


def cycle_marker_path() -> Path:
    """PID marker for a running build cycle — prevents the auto timer from
    starting a second overlapping cycle."""
    return _automod_home() / ".cycle-running"


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
    # 2026-06-24: desktop-tauri moved UNDER src/voice-agent/, bringing the Rust/UI
    # shell inside the auto-mod editable prefix. Keep it off-limits — the
    # self-evolution loop is for the Python brain, not the desktop app.
    "src/voice-agent/desktop-tauri/",
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
    outside the allowed prefix. Normalizes the path first so `..` traversal,
    quoting, and `./` prefixes cannot slip past. Used by throttle, finalize,
    and merge."""
    p = path.strip().strip('"').strip()
    if not p:
        return True  # empty/garbage path → fail closed
    p = posixpath.normpath(p)
    # Absolute, or escapes the repo root via a leading `..` → never allowed.
    if p.startswith("/") or p == ".." or p.startswith("../"):
        return True
    for blocked in HARD_BLOCKLIST_PATHS:
        if p == blocked.rstrip("/") or p.startswith(blocked):
            return True
    if not p.startswith(ALLOWED_PATH_PREFIX):
        return True
    return False
