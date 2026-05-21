"""Minimal Hermes-runtime shim for ported tool handlers.

Hermes tool handlers import process-level constants and path helpers from
``hermes_constants`` (``get_hermes_home``, ``get_hermes_dir``,
``get_subprocess_home``, ``display_hermes_home``, ``is_container`` …). When a
real Hermes tool is ported into this tree it should import those names from
HERE instead, so the JARVIS port re-homes Hermes state under ``~/.jarvis``
rather than ``~/.hermes``.

Deliberately TINY — this is the foundation wave. Grow it (add only the names a
ported tool actually needs) as real tools land. Keep it stdlib-only and
import-safe at module scope (handlers import it at load time).

Override the home dir for tests / alternate profiles with the ``JARVIS_HOME``
env var (mirrors Hermes' ``HERMES_HOME``).
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

__all__ = [
    "get_hermes_home",
    "get_hermes_dir",
    "get_subprocess_home",
    "display_hermes_home",
    "is_container",
]


# Env var that overrides the home directory (Hermes used HERMES_HOME).
_HOME_ENV = "JARVIS_HOME"
_DEFAULT_HOME = Path.home() / ".jarvis"


def get_hermes_home() -> Path:
    """Return the JARVIS home directory, creating it if missing.

    Reads ``JARVIS_HOME`` env var; falls back to ``~/.jarvis``. This is the
    single source of truth for ported Hermes handlers that previously called
    ``hermes_constants.get_hermes_home()`` — they now land their state under
    ``~/.jarvis`` alongside the bridge/hub state.
    """
    val = os.environ.get(_HOME_ENV, "").strip()
    home = Path(val) if val else _DEFAULT_HOME
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Never let a transient mkdir failure brick a module-level import; the
        # caller will surface the real error when it actually touches the path.
        pass
    return home


def get_hermes_dir(new_subpath: str, old_name: str = "") -> Path:
    """Return ``<home>/<new_subpath>``, creating it if missing.

    Hermes' signature took ``(new_subpath, old_name)`` to support a legacy→new
    directory migration. JARVIS has no legacy layout, so ``old_name`` is
    accepted-and-ignored purely to keep ported call sites compiling.
    """
    d = get_hermes_home() / new_subpath
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def get_subprocess_home() -> str | None:
    """Return the home dir to propagate to spawned subprocesses, as a string.

    Mirrors ``hermes_constants.get_subprocess_home``: a subprocess spawner sets
    ``JARVIS_HOME`` in the child env to this value so the child resolves the
    same home. Returns ``None`` when no explicit override is set (the child
    will fall back to ``~/.jarvis`` on its own).
    """
    val = os.environ.get(_HOME_ENV, "").strip()
    return val or None


def display_hermes_home() -> str:
    """Human-readable home path for logs/UX (``~/...`` collapsed when possible)."""
    home = get_hermes_home()
    try:
        rel = home.relative_to(Path.home())
        return str(Path("~") / rel)
    except ValueError:
        return str(home)


def is_container() -> bool:
    """Best-effort 'are we inside a container?' check.

    JARVIS runs on bare-metal Kali in practice; ported tools occasionally gate
    behavior on this. Cheap heuristics only — no daemon probes.
    """
    if os.environ.get("container"):
        return True
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8")
        if "docker" in cgroup or "kubepods" in cgroup or "containerd" in cgroup:
            return True
    except OSError:
        pass
    # WSL is not a container but some Hermes tools treat it adjacently; keep the
    # signal here behind an explicit check rather than conflating.
    return "microsoft" in platform.release().lower() and os.environ.get("WSL_DISTRO_NAME") is not None
