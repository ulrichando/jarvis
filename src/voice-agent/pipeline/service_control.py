"""Cross-platform service-control helper.

JARVIS service control is platform-dispatched:

* **Linux** — ``systemctl --user`` user units. The voice-agent
  (``jarvis-voice-agent``) and voice-client (``jarvis-voice-client``)
  both ship as user-mode systemd units; restarts go through
  ``systemctl --user restart <name>``. Several call sites in the
  voice-agent need to restart one or the other (the session-close
  crash watchdog, the presence watchdog, the model-swap path that
  bounces the agent unit).
* **Windows** — services managed via ``nssm`` (the Non-Sucking
  Service Manager). Phase 3.3's ``install.ps1`` will install
  ``nssm.exe`` to ``%LOCALAPPDATA%\\jarvis\\bin\\nssm.exe`` and
  register the voice-agent + voice-client services there. This
  module locates ``nssm.exe`` at runtime — preferring the install
  path, falling back to ``PATH`` — and shells out to it.
* **macOS** — not wired yet (we don't ship macOS service units).
  Raises :class:`ServiceControlError` with a "not supported" message.

Surface:

  * ``restart_service(name)``        — fire-and-forget restart (sync)
  * ``restart_service_async(name)``  — awaitable restart
  * ``start_service(name)``          — start the service (sync)
  * ``start_service_async(name)``    — awaitable start
  * ``stop_service(name)``           — stop the service (sync)
  * ``stop_service_async(name)``     — awaitable stop
  * ``service_status(name)``         — "RUNNING" | "STOPPED" | "UNKNOWN"
  * ``service_status_async(name)``   — awaitable status
  * ``restart_services_async(names)``— sequential multi-unit restart

Each function dispatches by ``platform.system()`` so a single call
site works on both Linux and Windows. The blocking forms use
``subprocess.Popen`` / ``subprocess.run``; the async forms use
``asyncio.create_subprocess_exec``.

Phase 3.1 wired the Windows nssm backend. Phase 3.3 will wire
``install.ps1`` to ``nssm install`` the services so the surface
here has something real to talk to.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("jarvis.pipeline.service_control")

__all__ = [
    "restart_service",
    "restart_service_async",
    "start_service",
    "start_service_async",
    "stop_service",
    "stop_service_async",
    "service_status",
    "service_status_async",
    "restart_services_async",
    "ServiceControlError",
]


# Hint surfaced when nssm.exe isn't installed on this host.
_NSSM_MISSING_HINT = (
    "nssm.exe not found — install via install.ps1 or download from "
    "https://nssm.cc/download. install.ps1 places it at "
    "%LOCALAPPDATA%\\jarvis\\bin\\nssm.exe; alternatively put it on PATH."
)


class ServiceControlError(RuntimeError):
    """Raised when service control fails or isn't wired for this platform.

    Catch this in callers that can degrade gracefully (e.g. the
    presence-watchdog can log and exit instead of crashing the worker).
    """


# ── Linux backend (systemctl --user) ──────────────────────────────────

def _systemctl_argv(verb: str, name: str) -> list[str]:
    """The Linux ``systemctl --user <verb> <name>`` argv."""
    return ["systemctl", "--user", verb, name]  # windows-footgun: ok (Linux backend, dispatched via platform.system() check)


def _linux_restart(name: str) -> None:
    """Linux restart: spawn systemctl --user restart <name> fire-and-forget."""
    subprocess.Popen(
        _systemctl_argv("restart", name),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _linux_restart_async(name: str) -> int | None:
    """Linux async restart: await systemctl --user restart <name>."""
    proc = await asyncio.create_subprocess_exec(
        *_systemctl_argv("restart", name),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode


def _linux_start(name: str) -> None:
    """Linux start: spawn systemctl --user start <name> fire-and-forget."""
    subprocess.Popen(
        _systemctl_argv("start", name),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _linux_start_async(name: str) -> int | None:
    proc = await asyncio.create_subprocess_exec(
        *_systemctl_argv("start", name),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode


def _linux_stop(name: str) -> None:
    subprocess.Popen(
        _systemctl_argv("stop", name),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _linux_stop_async(name: str) -> int | None:
    proc = await asyncio.create_subprocess_exec(
        *_systemctl_argv("stop", name),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode


def _linux_status(name: str) -> str:
    """Linux status: systemctl --user is-active <name>.

    ``is-active`` exits 0 + prints "active" when running, non-zero +
    prints "inactive" / "failed" / "unknown" otherwise. We map to the
    canonical "RUNNING" / "STOPPED" / "UNKNOWN" set the cross-platform
    surface promises.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", name],  # windows-footgun: ok (Linux backend, dispatched via platform.system() check)
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return "UNKNOWN"
    state = (proc.stdout or "").strip().lower()
    if state == "active":
        return "RUNNING"
    if state in ("inactive", "failed", "deactivating", "activating"):
        return "STOPPED"
    return "UNKNOWN"


async def _linux_status_async(name: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "is-active", name,  # windows-footgun: ok (Linux backend, dispatched via platform.system() check)
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except (asyncio.TimeoutError, OSError):
        return "UNKNOWN"
    state = out_b.decode("utf-8", errors="replace").strip().lower()
    if state == "active":
        return "RUNNING"
    if state in ("inactive", "failed", "deactivating", "activating"):
        return "STOPPED"
    return "UNKNOWN"


# ── Windows backend (nssm) ────────────────────────────────────────────

def _locate_nssm() -> str:
    """Locate ``nssm.exe`` on a Windows host.

    Lookup order:
      1. ``%LOCALAPPDATA%\\jarvis\\bin\\nssm.exe`` — where Phase 3.3's
         ``install.ps1`` installs it.
      2. ``shutil.which("nssm")`` — generic PATH lookup.
      3. ``shutil.which("nssm.exe")`` — explicit ``.exe`` PATH lookup.

    Raises :class:`ServiceControlError` with an installer hint when
    nothing is found. Callers receive a clear "install nssm" signal
    rather than a cryptic ``FileNotFoundError``.
    """
    # 1. Install-path under %LOCALAPPDATA% (where install.ps1 will place it).
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        candidate = Path(localappdata) / "jarvis" / "bin" / "nssm.exe"
        if candidate.is_file():
            return str(candidate)
    # 2 + 3. PATH lookup.
    for name in ("nssm", "nssm.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise ServiceControlError(_NSSM_MISSING_HINT)


def _nssm_run_sync(verb: str, name: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run ``nssm <verb> <name>`` synchronously and return the result.

    Locates ``nssm.exe`` first (may raise ServiceControlError if missing).
    Captures stdout + stderr so the caller can include nssm's error
    message in the exception it raises.
    """
    nssm = _locate_nssm()
    return subprocess.run(
        [nssm, verb, name],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


async def _nssm_run_async(verb: str, name: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Async variant of :func:`_nssm_run_sync`.

    Returns ``(returncode, stdout, stderr)``. Locates nssm.exe up
    front; may raise ServiceControlError if missing.
    """
    nssm = _locate_nssm()
    proc = await asyncio.create_subprocess_exec(
        nssm, verb, name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # Best-effort cleanup; nssm should normally return well under 30s.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise ServiceControlError(
            f"nssm {verb} {name} timed out after {timeout}s"
        )
    rc = proc.returncode if proc.returncode is not None else -1
    return (
        rc,
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
    )


def _nssm_status_to_canonical(stdout: str) -> str:
    """Map ``nssm status`` output to RUNNING / STOPPED / UNKNOWN.

    nssm prints text like ``SERVICE_RUNNING`` / ``SERVICE_STOPPED`` /
    ``SERVICE_PAUSED`` / ``SERVICE_START_PENDING`` /
    ``SERVICE_STOP_PENDING``. We collapse to the three states the
    cross-platform surface promises.
    """
    text = (stdout or "").strip().upper()
    if "SERVICE_RUNNING" in text:
        return "RUNNING"
    if "SERVICE_STOPPED" in text:
        return "STOPPED"
    # PAUSED / START_PENDING / STOP_PENDING etc. — treat as transitional;
    # callers that care can poll again. UNKNOWN signals "don't act yet".
    return "UNKNOWN"


def _windows_restart(name: str) -> None:
    """Windows restart via nssm. Raises ServiceControlError on failure."""
    proc = _nssm_run_sync("restart", name)
    if proc.returncode != 0:
        raise ServiceControlError(
            f"nssm restart {name} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )


async def _windows_restart_async(name: str) -> int | None:
    rc, stdout, stderr = await _nssm_run_async("restart", name)
    if rc != 0:
        raise ServiceControlError(
            f"nssm restart {name} failed (exit {rc}): "
            f"{(stderr or stdout).strip()}"
        )
    return rc


def _windows_start(name: str) -> None:
    proc = _nssm_run_sync("start", name)
    if proc.returncode != 0:
        raise ServiceControlError(
            f"nssm start {name} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )


async def _windows_start_async(name: str) -> int | None:
    rc, stdout, stderr = await _nssm_run_async("start", name)
    if rc != 0:
        raise ServiceControlError(
            f"nssm start {name} failed (exit {rc}): "
            f"{(stderr or stdout).strip()}"
        )
    return rc


def _windows_stop(name: str) -> None:
    proc = _nssm_run_sync("stop", name)
    if proc.returncode != 0:
        raise ServiceControlError(
            f"nssm stop {name} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )


async def _windows_stop_async(name: str) -> int | None:
    rc, stdout, stderr = await _nssm_run_async("stop", name)
    if rc != 0:
        raise ServiceControlError(
            f"nssm stop {name} failed (exit {rc}): "
            f"{(stderr or stdout).strip()}"
        )
    return rc


def _windows_status(name: str) -> str:
    """Windows status via ``nssm status``. Returns RUNNING / STOPPED / UNKNOWN.

    A non-zero exit from ``nssm status`` means the service doesn't exist
    (or nssm has an internal problem) — we surface that as STOPPED so
    callers treat "service absent" the same as "service not running"
    (both mean "don't expect it to be doing work"). Use the install
    path for "is it installed?" checks instead.
    """
    try:
        proc = _nssm_run_sync("status", name)
    except ServiceControlError:
        # nssm itself isn't installed — UNKNOWN, not STOPPED. Callers
        # that propagate this can distinguish "service down" from
        # "we can't see the service state."
        raise
    if proc.returncode != 0:
        return "STOPPED"
    return _nssm_status_to_canonical(proc.stdout)


async def _windows_status_async(name: str) -> str:
    rc, stdout, _stderr = await _nssm_run_async("status", name)
    if rc != 0:
        return "STOPPED"
    return _nssm_status_to_canonical(stdout)


# ── Public surface (platform dispatch) ────────────────────────────────


def _unsupported(platform_name: str, fn_name: str, unit: str) -> ServiceControlError:
    return ServiceControlError(
        f"{fn_name}({unit!r}) on {platform_name}: unsupported platform "
        "(JARVIS ships service units for Linux and Windows only)."
    )


def restart_service(name: str) -> None:
    """Restart a JARVIS service (fire-and-forget on Linux, blocking on Windows).

    Args:
        name: Service / unit name (without ``.service`` on Linux). On
              Windows, the nssm-registered service name (matches the
              Linux unit name in install.ps1).

    Raises:
        ServiceControlError: on Windows if nssm isn't installed, if
            ``nssm restart`` exits non-zero, or on platforms we don't
            ship units for (macOS).
    """
    sys = platform.system()
    if sys == "Linux":
        _linux_restart(name)
        return
    if sys == "Windows":
        _windows_restart(name)
        return
    raise _unsupported(sys, "restart_service", name)


async def restart_service_async(name: str) -> int | None:
    """Async restart of a JARVIS service.

    Returns:
        The backend's exit code (systemctl on Linux, nssm on Windows).
        ``None`` only on an exotic codepath where the child exits
        without setting a returncode; treat as failure.

    Raises:
        ServiceControlError: as for :func:`restart_service`.
    """
    sys = platform.system()
    if sys == "Linux":
        return await _linux_restart_async(name)
    if sys == "Windows":
        return await _windows_restart_async(name)
    raise _unsupported(sys, "restart_service_async", name)


def start_service(name: str) -> None:
    """Start a JARVIS service.

    Raises ServiceControlError on unsupported platforms or backend failure.
    """
    sys = platform.system()
    if sys == "Linux":
        _linux_start(name)
        return
    if sys == "Windows":
        _windows_start(name)
        return
    raise _unsupported(sys, "start_service", name)


async def start_service_async(name: str) -> int | None:
    sys = platform.system()
    if sys == "Linux":
        return await _linux_start_async(name)
    if sys == "Windows":
        return await _windows_start_async(name)
    raise _unsupported(sys, "start_service_async", name)


def stop_service(name: str) -> None:
    """Stop a JARVIS service.

    Raises ServiceControlError on unsupported platforms or backend failure.
    """
    sys = platform.system()
    if sys == "Linux":
        _linux_stop(name)
        return
    if sys == "Windows":
        _windows_stop(name)
        return
    raise _unsupported(sys, "stop_service", name)


async def stop_service_async(name: str) -> int | None:
    sys = platform.system()
    if sys == "Linux":
        return await _linux_stop_async(name)
    if sys == "Windows":
        return await _windows_stop_async(name)
    raise _unsupported(sys, "stop_service_async", name)


def service_status(name: str) -> str:
    """Return ``"RUNNING"`` | ``"STOPPED"`` | ``"UNKNOWN"`` for a service.

    Same semantics across platforms:

      * ``RUNNING`` — unit is active / SERVICE_RUNNING.
      * ``STOPPED`` — unit is inactive / SERVICE_STOPPED / not installed.
      * ``UNKNOWN`` — transitional state, probe failed, or systemctl /
        nssm returned an unparseable value. Callers should treat as
        "don't act yet" and re-poll.

    Raises ServiceControlError on unsupported platforms.
    """
    sys = platform.system()
    if sys == "Linux":
        return _linux_status(name)
    if sys == "Windows":
        return _windows_status(name)
    raise _unsupported(sys, "service_status", name)


async def service_status_async(name: str) -> str:
    sys = platform.system()
    if sys == "Linux":
        return await _linux_status_async(name)
    if sys == "Windows":
        return await _windows_status_async(name)
    raise _unsupported(sys, "service_status_async", name)


async def restart_services_async(names: Iterable[str], gap_seconds: float = 0.0) -> None:
    """Sequentially restart multiple units with an optional gap between each.

    Use case: the agent model-swap path restarts ``jarvis-voice-agent``,
    waits a few seconds for the worker to re-register, then restarts
    ``jarvis-voice-client`` to force a fresh dispatch. Each call goes
    through :func:`restart_service_async`, so the same platform dispatch
    applies.

    Errors from one restart are logged but don't abort the sequence —
    the caller (a fire-and-forget background task) doesn't have a sane
    recovery path either way.
    """
    first = True
    for name in names:
        if not first and gap_seconds > 0:
            await asyncio.sleep(gap_seconds)
        first = False
        try:
            rc = await restart_service_async(name)
            if rc not in (None, 0):
                logger.warning("restart_service_async(%s) returned %s", name, rc)
        except ServiceControlError as e:
            logger.warning("restart_service_async(%s) unsupported: %s", name, e)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("restart_service_async(%s) failed: %s", name, e)
