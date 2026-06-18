"""Cross-platform sd_notify shim.

The systemd notify protocol (``READY=1`` / ``WATCHDOG=1`` / ``STOPPING=1``)
is a Linux+systemd concept. The voice-agent imported ``sdnotify``
unconditionally (``resilience/watchdog.py``, ``jarvis_agent.py`` ``__main__``),
which is a hard dependency that doesn't belong on Windows/macOS — where there
is no ``$NOTIFY_SOCKET`` and the package may not even be installed.

``get_notifier()`` returns a ``SystemdNotifier``-like object exposing
``.notify(state: str)``:

* **Linux** — a real ``sdnotify.SystemdNotifier`` (which is itself already a
  no-op when not running under systemd — it just writes to the socket named by
  ``$NOTIFY_SOCKET`` if present).
* **Windows / macOS, or ``sdnotify`` not installed** — a no-op notifier, so
  the watchdog loop and the worker entrypoint run unchanged minus the (absent)
  systemd integration.

The Linux behaviour is therefore identical to the previous direct
``sdnotify.SystemdNotifier()`` use; only the non-Linux/absent paths change
(from ``ImportError`` at startup to a silent no-op). Service supervision on
Windows is handled separately by ``pipeline.service_control`` (nssm).
"""
from __future__ import annotations

import logging
import platform

logger = logging.getLogger("jarvis.notify")


class _NoopNotifier:
    """sd_notify has no meaning off Linux/systemd — accept + drop every state."""

    def notify(self, state: str) -> None:
        return None


def get_notifier():
    """Return a ``.notify(state)`` object: real sdnotify on Linux, else no-op."""
    if platform.system() != "Linux":
        return _NoopNotifier()
    try:
        import sdnotify  # windows-footgun: ok (Linux backend of this notify shim; systemd-only)
        return sdnotify.SystemdNotifier()
    except Exception as exc:  # noqa: BLE001 - absent/broken sdnotify must not crash boot
        logger.debug("sdnotify unavailable (%s); notify is a no-op", exc)
        return _NoopNotifier()
