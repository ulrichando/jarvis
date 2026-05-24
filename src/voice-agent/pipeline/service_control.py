"""Cross-platform service-control helper (Phase 2.3 abstraction).

JARVIS service control is currently Linux-only — both ``jarvis-voice-agent``
and ``jarvis-voice-client`` run as ``systemctl --user`` units. Several
call sites across the voice-agent need to restart one or the other (the
session-close crash watchdog, the presence watchdog, the model-swap path
that bounces the agent unit). On Windows none of those paths can shell out
to ``systemctl``; calling it would either crash or — worse — succeed
silently against an unrelated PATH binary.

This helper dispatches by ``platform.system()``:

  Linux:   ``systemctl --user restart <name>`` (preserved exactly).
  Windows: ``NotImplementedError`` pointing to the Phase 3 follow-up
           (``install.ps1`` + ``nssm`` integration). The follow-up will
           swap the Windows branch out for a real ``nssm restart`` /
           ``sc.exe`` call.
  macOS:   same NotImplementedError shape; we don't ship macOS service
           units yet either.

The blocking ``restart_service`` form replaces ``subprocess.Popen(...)`` /
``subprocess.run([...])`` patterns; the asyncio-friendly
``restart_service_async`` replaces ``asyncio.create_subprocess_exec(...)``.
Both share the same backend.

Spec context: Phase 2.3 of the cross-platform footgun cleanup. The
Phase 1 ``install.ps1`` ships CLI + Desktop UI; the voice-agent service
install is the Phase 3 deliverable.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
from typing import Iterable

logger = logging.getLogger("jarvis.pipeline.service_control")

__all__ = [
    "restart_service",
    "restart_service_async",
    "restart_services_async",
    "ServiceControlError",
]


_PHASE3_HINT = (
    "Service control on Windows requires Phase 3 — install.ps1 + nssm "
    "integration (see docs/superpowers/specs/2026-05-23-windows-install-"
    "phase1-design.md). Until then the voice-agent service install is "
    "deferred and restart_service is a no-op on non-Linux."
)


class ServiceControlError(RuntimeError):
    """Raised when service-control isn't wired for this platform yet.

    Catch this in callers that can degrade gracefully (e.g. the
    presence-watchdog can log and exit instead of crashing the worker).
    """


def _systemctl_argv(name: str) -> list[str]:
    """The Linux ``systemctl --user restart <name>`` argv."""
    return ["systemctl", "--user", "restart", name]  # windows-footgun: ok (Linux backend, dispatched via platform.system() check)


def restart_service(name: str) -> None:
    """Restart a JARVIS systemd user unit (fire-and-forget).

    Linux: spawns ``systemctl --user restart <name>`` via subprocess.Popen
    so the caller doesn't block on systemd's reload. stdout + stderr are
    silenced; check journalctl for failures.

    Windows / macOS: raises :class:`ServiceControlError` (Phase 3 will
    fill in the backend).

    Args:
        name: Systemd unit name without the ``.service`` suffix
              (e.g. ``"jarvis-voice-agent"`` / ``"jarvis-voice-client"``).
    """
    if platform.system() == "Linux":
        subprocess.Popen(
            _systemctl_argv(name),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    raise ServiceControlError(
        f"restart_service({name!r}) on {platform.system()}: {_PHASE3_HINT}"
    )


async def restart_service_async(name: str) -> int | None:
    """Async restart of a JARVIS systemd user unit.

    Linux: awaits ``systemctl --user restart <name>`` via
    asyncio.create_subprocess_exec and returns the exit code. Stdout +
    stderr are silenced — callers that need diagnostics should query
    systemd directly (``systemctl --user status <name>`` / journalctl).

    Windows / macOS: raises :class:`ServiceControlError` (Phase 3).

    Returns:
        The systemctl exit code on Linux. ``None`` is unreachable on
        Linux (kept in the return type to make the no-op shape explicit
        once we add the Windows backend in Phase 3 — the success path
        will likely return ``0`` there too).
    """
    if platform.system() == "Linux":
        proc = await asyncio.create_subprocess_exec(
            *_systemctl_argv(name),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode
    raise ServiceControlError(
        f"restart_service_async({name!r}) on {platform.system()}: {_PHASE3_HINT}"
    )


async def restart_services_async(names: Iterable[str], gap_seconds: float = 0.0) -> None:
    """Sequentially restart multiple units with an optional gap between each.

    Use case: the agent model-swap path restarts ``jarvis-voice-agent``,
    waits a few seconds for the worker to re-register, then restarts
    ``jarvis-voice-client`` to force a fresh dispatch. Each call goes
    through :func:`restart_service_async`, so the same Linux-vs-Phase3
    dispatch applies.

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
