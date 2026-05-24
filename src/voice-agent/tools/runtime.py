"""Runtime path helpers for JARVIS tool handlers.

Tool handlers import process-level constants and path helpers from this module
(``get_jarvis_home``, ``get_jarvis_dir``, ``get_subprocess_home``,
``display_jarvis_home``, ``is_container`` …). Tools ported into this tree
import these names so their state lands under ``~/.jarvis`` alongside the
rest of the per-user voice-agent state.

Deliberately TINY — this is the foundation wave. Grow it (add only the names a
tool actually needs) as real tools land. Keep it stdlib-only and import-safe at
module scope (handlers import it at load time).

Override the home dir for tests / alternate profiles with the ``JARVIS_HOME``
env var.
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

__all__ = [
    "get_jarvis_home",
    "get_jarvis_dir",
    "get_jarvis_data_dir",
    "get_jarvis_log_dir",
    "get_jarvis_models_dir",
    "get_subprocess_home",
    "display_jarvis_home",
    "is_container",
    "detached_popen_kwargs",
    "is_process_running",
]


# Env var that overrides the home directory.
_HOME_ENV = "JARVIS_HOME"
_DEFAULT_HOME = Path.home() / ".jarvis"


def _is_windows() -> bool:
    """Late-binding platform check so tests can monkeypatch platform.system."""
    return platform.system() == "Windows"


def get_jarvis_home() -> Path:
    """Return the JARVIS home directory, creating it if missing.

    Reads ``JARVIS_HOME`` env var; falls back to ``~/.jarvis``. This is the
    single source of truth for tool handlers that need to land state under
    the per-user voice-agent root.
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


def get_jarvis_dir(new_subpath: str, old_name: str = "") -> Path:
    """Return ``<home>/<new_subpath>``, creating it if missing.

    ``old_name`` is accepted-and-ignored to keep ported call sites compiling
    when the upstream tool used a legacy→new directory migration signature.
    JARVIS has no legacy layout.
    """
    d = get_jarvis_home() / new_subpath
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def get_jarvis_data_dir() -> Path:
    """Return the JARVIS per-user data directory, creating it if missing.

    Linux/macOS: ``~/.local/share/jarvis`` (XDG_DATA_HOME-compatible).
    Windows: ``%LOCALAPPDATA%\\jarvis\\data``.

    Honours ``JARVIS_DATA_DIR`` for tests / alternate profiles. This is
    where logs, the telemetry SQLite, screenshot dumps, and the batch
    runner's per-run output land — the larger / longer-lived state, as
    opposed to ``get_jarvis_home()`` which holds keys + auth tokens +
    user-supplied config.
    """
    val = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if val:
        d = Path(val)
    elif _is_windows():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        d = Path(base) / "jarvis" / "data"
    else:
        # XDG_DATA_HOME default per spec is ~/.local/share
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        d = base / "jarvis"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def get_jarvis_log_dir() -> Path:
    """Return the JARVIS log directory, creating it if missing.

    Linux/macOS: ``~/.local/share/jarvis/logs``.
    Windows: ``%LOCALAPPDATA%\\jarvis\\data\\logs``.

    Lives under :func:`get_jarvis_data_dir` so the same rotation /
    archival policy applies; pulled out as its own helper so callers that
    only want a log path don't have to remember the ``/"logs"`` suffix.
    """
    d = get_jarvis_data_dir() / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def get_jarvis_models_dir() -> Path:
    """Return the voice-agent ``models/`` directory, creating it if missing.

    Always resolved relative to the voice-agent install root (this file's
    grand-parent) — the local model artifacts ship inside the source tree,
    not under per-user state. Same path on every platform.
    """
    # runtime.py lives at src/voice-agent/tools/runtime.py
    # → parent.parent = src/voice-agent/
    d = Path(__file__).resolve().parent.parent / "models"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def get_subprocess_home() -> str | None:
    """Return the home dir to propagate to spawned subprocesses, as a string.

    A subprocess spawner sets ``JARVIS_HOME`` in the child env to this value
    so the child resolves the same home. Returns ``None`` when no explicit
    override is set (the child will fall back to ``~/.jarvis`` on its own).
    """
    val = os.environ.get(_HOME_ENV, "").strip()
    return val or None


def display_jarvis_home() -> str:
    """Human-readable home path for logs/UX (``~/...`` collapsed when possible)."""
    home = get_jarvis_home()
    try:
        rel = home.relative_to(Path.home())
        return str(Path("~") / rel)
    except ValueError:
        return str(home)


def detached_popen_kwargs() -> dict:
    """Return subprocess.Popen kwargs that detach the child into its own session/group.

    The voice-agent's ``launch_app`` GUI launcher needs the child process to
    SURVIVE a worker bounce — without detachment, restarting the agent
    immediately kills any browsers / editors the user just asked JARVIS to
    open. On Linux the canonical way is ``setsid`` (or the equivalent
    ``start_new_session=True`` Popen kwarg, which calls ``setsid`` under the
    hood). On Windows the equivalent is the ``CREATE_NEW_PROCESS_GROUP``
    plus ``DETACHED_PROCESS`` creationflags.

    Returns a kwargs dict suitable for ``**`` splatting into
    ``subprocess.Popen`` / ``asyncio.create_subprocess_exec``:

      Linux/macOS:  ``{"start_new_session": True}``
      Windows:      ``{"creationflags": CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS}``

    Both branches achieve "child outlives parent" semantics; the platform-
    specific flag names are the only difference.
    """
    if _is_windows():
        # Both constants are stdlib on Windows; gate on _is_windows() so the
        # attribute access is only evaluated on the platform where it exists.
        return {
            "creationflags": (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        }
    return {"start_new_session": True}


def is_process_running(name_pattern: str) -> list[int]:
    """Return PIDs whose process name or cmdline contains ``name_pattern``.

    Cross-platform substring match (case-insensitive) against both the
    process's ``name`` and its full ``cmdline`` (joined). Used by the
    launch_app post-launch verifier in place of the Linux-only
    ``pgrep -f <name>`` shellout — pgrep doesn't exist on Windows and
    a ``shutil.which("pgrep")`` gate would just silently fall through
    to "not running" on every Windows host.

    Args:
        name_pattern: Substring to look for. Matched case-insensitively
                      against process name and joined cmdline.

    Returns:
        List of PIDs matching the pattern. Empty list on no match
        OR on any error (psutil import failure, iteration failure, etc.)
        — the function never raises, so callers can use it as a simple
        "is this thing alive?" probe without try/except.
    """
    # Local import so the module stays import-safe even on hosts where
    # psutil isn't yet installed (e.g. a fresh checkout before
    # ``pip install -r requirements.txt``). psutil IS a runtime
    # dependency per requirements.txt; this guard is belt-and-suspenders.
    try:
        import psutil
    except ImportError:
        return []
    if not name_pattern:
        return []
    pattern_lower = name_pattern.lower()
    pids: list[int] = []
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                name = (info.get("name") or "").lower()
                cmdline_parts = info.get("cmdline") or []
                cmdline = " ".join(cmdline_parts).lower()
                if pattern_lower in name or pattern_lower in cmdline:
                    pid = info.get("pid")
                    if pid is not None:
                        pids.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Process disappeared mid-iteration, or we don't have
                # permission to read it. Skip silently — the goal is
                # a best-effort probe, not an audit.
                continue
            except Exception:
                # Don't let one weird proc abort the whole scan.
                continue
    except Exception:
        # process_iter() itself blew up — return what we have so far
        # (empty if it failed before yielding anything).
        return pids
    return pids


def is_container() -> bool:
    """Best-effort 'are we inside a container?' check.

    JARVIS runs on bare-metal Kali in practice; tools occasionally gate
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
    # WSL is not a container but some tools treat it adjacently; keep the
    # signal here behind an explicit check rather than conflating.
    return "microsoft" in platform.release().lower() and os.environ.get("WSL_DISTRO_NAME") is not None
