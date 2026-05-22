"""JARVIS-native cloud-browser provider base.

A tiny, JARVIS-native abstraction for *remote* cloud browsers (Browserbase,
Firecrawl, …) that the local ``browser_task`` tool can optionally drive over
CDP. Ported from the upstream browser-provider ABC, stripped of the
``agent.*`` / gateway / ``config.yaml`` coupling: availability is env-key
driven via each provider's own ``is_available()``.

Lifecycle contract (duck-typed; the generic ``_provider_registry`` only ever
stores + resolves these — it never calls the lifecycle methods):

  * ``name``                 — stable lowercase identifier (str class attr).
  * ``is_available()``       — cheap, no-network bool gate (API key present).
  * ``create_session(task_id)`` — open a remote browser, return a dict with at
    least ``{"cdp_url": <ws/http CDP connect url>, "session_id": <id>}``.
    May raise on missing creds / network failure; the consuming tool catches
    and degrades to a clean ``tool_error``.
  * ``close_session(session_id)`` — release the session; returns bool; never
    raises (log + return False).
  * ``emergency_cleanup(session_id)`` — best-effort teardown for atexit /
    signal paths; never raises.

This is consumed exclusively by :mod:`tools.browser` when the operator opts in
via ``JARVIS_BROWSER_PROVIDER``. With that env unset the local subprocess path
in ``browser_task`` is used unchanged and this base is never touched.

Stdlib-only and import-safe at module scope.
"""
from __future__ import annotations

import abc
from typing import Any, Dict


class BrowserProvider(abc.ABC):
    """Abstract base for a remote cloud-browser backend.

    Subclasses must set :attr:`name` and implement :meth:`is_available`,
    :meth:`create_session`, and :meth:`close_session`. :meth:`emergency_cleanup`
    has a no-op default so subclasses only override it when they hold a session
    handle worth releasing on process exit.
    """

    #: Stable lowercase identifier, unique within the ``browser`` kind.
    name: str = ""

    @property
    def display_name(self) -> str:
        """Human-readable label. Defaults to :attr:`name`."""
        return self.name

    @abc.abstractmethod
    def is_available(self) -> bool:
        """True when this provider can service calls.

        Cheap, no-network check (env key present). Must NOT make network calls
        — it runs at tool-gate time, potentially on every supervisor paint.
        """

    @abc.abstractmethod
    def create_session(self, task_id: str) -> Dict[str, Any]:
        """Open a remote browser session and return its metadata.

        Must return a dict with at least::

            {"cdp_url": <CDP connect url>, "session_id": <provider session id>}

        May raise ``ValueError`` (missing credentials) or ``RuntimeError``
        (network / API failure); the consuming tool surfaces these as a clean
        error and never lets them crash the turn.
        """

    @abc.abstractmethod
    def close_session(self, session_id: str) -> bool:
        """Release / terminate a session by id. Returns True on success.

        Must not raise — log and return False on any exception so a cleanup
        loop keeps moving.
        """

    def emergency_cleanup(self, session_id: str) -> None:  # noqa: B027 — intentional no-op default
        """Best-effort teardown during process exit. Default: no-op. Never raises."""
        return None
