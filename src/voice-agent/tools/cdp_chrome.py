"""Singleton lifecycle manager for the Playwright Chromium used by
the browser subagent's CDP fallback path.

When the Chrome extension at `~/Documents/.../src/extensions/jarvis-screen`
isn't connected to the bridge, the browser subagent routes its tool
calls through tools/browser_cdp.py, which delegates here for a live
Playwright `Page` instance.

Design choices (see docs/superpowers/specs/2026-05-17-browser-cdp-fallback-design.md):

- **Persistent user_data_dir at `~/.jarvis/cdp-profile/`** — preserves
  the user's logins across CDP runs without racing the main Chrome
  profile lock.
- **Lazy spawn** — Chromium isn't launched until the first `get_page()`
  call. Saves RAM when the extension path is healthy and never fires
  the fallback.
- **Idle shutdown after 5 min** — a background task watches the
  last-activity timestamp; idle browser is closed automatically.
  Bounds memory cost in steady-state.
- **Visible window by default** — matches the UX of the extension
  path (user can see what JARVIS is doing). Headless via
  `JARVIS_CDP_HEADLESS=1` for tests / unattended runs.
- **Window title "JARVIS Browser"** — distinguishes from the user's
  main Chrome in the taskbar.

Thread safety: a single `asyncio.Lock` guards the spawn path. All
async; never call sync.

Tested via mocked `async_playwright()` in tests/test_browser_cdp.py.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import time
from pathlib import Path
from typing import Optional


__all__ = ["CdpChrome", "get_cdp_chrome", "shutdown_cdp_chrome"]


logger = logging.getLogger("jarvis.tools.cdp_chrome")


# ── Configuration (env-overridable) ─────────────────────────────────

_PROFILE_DIR = Path(
    os.environ.get("JARVIS_CDP_PROFILE_DIR", str(Path.home() / ".jarvis" / "cdp-profile"))
)
_HEADLESS = os.environ.get("JARVIS_CDP_HEADLESS", "0") == "1"
_IDLE_SHUTDOWN_S = float(os.environ.get("JARVIS_CDP_IDLE_SHUTDOWN_S", "300"))
_IDLE_CHECK_INTERVAL_S = 30.0
_LAUNCH_TIMEOUT_S = 30.0  # cold Chromium boot + first page can take this long
_WINDOW_NAME = "JARVIS Browser"


class CdpChrome:
    """Singleton Playwright Chromium manager.

    Use the module-level `get_cdp_chrome()` accessor; constructing
    this class directly bypasses the singleton lock.
    """

    def __init__(self) -> None:
        self._playwright = None        # async_playwright() context
        self._context = None           # BrowserContext from launch_persistent_context
        self._spawn_lock = asyncio.Lock()
        self._idle_task: Optional[asyncio.Task] = None
        self._last_activity_ts: float = 0.0
        self._shutdown_in_progress = False

    @property
    def is_alive(self) -> bool:
        """True if a Chromium process is currently running. Doesn't
        probe the process — just checks whether we hold a context
        reference. If Chromium crashed under us, `get_page()` will
        detect and respawn on next call.
        """
        return self._context is not None

    async def _ensure_spawned(self) -> None:
        """Idempotent: launch Chromium if not already running.

        Held under `_spawn_lock` so concurrent first-callers don't
        spawn two Chromium processes. Sets `_last_activity_ts` and
        starts the idle-watchdog task on first spawn.
        """
        async with self._spawn_lock:
            if self._context is not None:
                return  # someone beat us to it
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(
                f"[cdp-chrome] spawning chromium (profile={_PROFILE_DIR}, "
                f"headless={_HEADLESS})"
            )
            # Lazy import — Playwright is a heavy dep and we want
            # import-time failures (when the binary isn't installed)
            # to surface only when the fallback actually fires, not
            # at voice-agent startup. The module is still importable
            # without Playwright; the spawn raises a clear message.
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                raise RuntimeError(
                    "playwright not installed — run `pip install playwright "
                    "&& playwright install chromium` in the voice-agent venv. "
                    "Or disable the CDP fallback by ensuring the Chrome "
                    "extension is connected."
                ) from e

            self._playwright = await async_playwright().start()
            try:
                self._context = await asyncio.wait_for(
                    self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(_PROFILE_DIR),
                        headless=_HEADLESS,
                        args=[
                            f"--window-name={_WINDOW_NAME}",
                            # No --remote-debugging-port — Playwright drives
                            # via its private CDP pipe, doesn't expose 9222
                            # to other processes.
                        ],
                        # Don't use the system Chrome — Playwright's bundled
                        # Chromium has a known stable CDP surface.
                        channel=None,
                    ),
                    timeout=_LAUNCH_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                await self._playwright.stop()
                self._playwright = None
                raise RuntimeError(
                    f"chromium launch timed out after {_LAUNCH_TIMEOUT_S}s — "
                    f"check if chromium binary is installed (`playwright "
                    f"install chromium`) and that the profile dir at "
                    f"{_PROFILE_DIR} isn't locked by another process."
                )
            self._mark_active()
            if self._idle_task is None or self._idle_task.done():
                self._idle_task = asyncio.create_task(self._idle_watchdog())

    def _mark_active(self) -> None:
        """Bump the last-activity timestamp. Called on every action."""
        self._last_activity_ts = time.monotonic()

    async def _idle_watchdog(self) -> None:
        """Background task: every `_IDLE_CHECK_INTERVAL_S`, check if
        Chromium has been idle for `_IDLE_SHUTDOWN_S` and close it if
        so. Exits cleanly on shutdown.
        """
        try:
            while self._context is not None and not self._shutdown_in_progress:
                await asyncio.sleep(_IDLE_CHECK_INTERVAL_S)
                idle_for = time.monotonic() - self._last_activity_ts
                if idle_for >= _IDLE_SHUTDOWN_S:
                    logger.info(
                        f"[cdp-chrome] idle for {idle_for:.0f}s ≥ "
                        f"{_IDLE_SHUTDOWN_S}s; closing chromium to free RAM"
                    )
                    await self.shutdown()
                    return
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[cdp-chrome] idle watchdog crashed")

    async def get_page(self):
        """Return the active page, spawning Chromium + creating a
        first page on cold start. Re-uses the existing active page on
        subsequent calls — supervisor's tool calls happen on the
        same tab the user is looking at.

        Detects a crashed browser (context.pages raises) and respawns
        once. If the respawn also fails, raises.
        """
        await self._ensure_spawned()
        self._mark_active()
        try:
            pages = self._context.pages
        except Exception as e:
            # Browser died under us — clear state and respawn once.
            logger.warning(f"[cdp-chrome] context unhealthy ({e!r}); respawning")
            await self._force_close_silently()
            await self._ensure_spawned()
            pages = self._context.pages
        if not pages:
            return await self._context.new_page()
        return pages[-1]  # most recently-active tab

    async def new_page(self, url: Optional[str] = None):
        """Open a brand-new tab. Equivalent to Ctrl+T."""
        await self._ensure_spawned()
        self._mark_active()
        page = await self._context.new_page()
        if url:
            await page.goto(url)
        return page

    async def _force_close_silently(self) -> None:
        """Internal: drop context + playwright refs without raising.
        Used during respawn after a crash."""
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._playwright = None

    async def shutdown(self) -> None:
        """Close Chromium + cancel watchdog. Safe to call multiple
        times. Called by atexit, by idle watchdog, and by tests."""
        self._shutdown_in_progress = True
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
            try:
                await self._idle_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._force_close_silently()
        self._shutdown_in_progress = False


# ── Module-level singleton ──────────────────────────────────────────

_singleton: Optional[CdpChrome] = None
_singleton_lock = asyncio.Lock()


async def get_cdp_chrome() -> CdpChrome:
    """Get-or-create the module singleton. Thread-safe under asyncio."""
    global _singleton
    async with _singleton_lock:
        if _singleton is None:
            _singleton = CdpChrome()
        return _singleton


async def shutdown_cdp_chrome() -> None:
    """Shut down the singleton if it exists. Used by atexit + tests."""
    global _singleton
    if _singleton is not None:
        await _singleton.shutdown()


def _atexit_handler() -> None:
    """Sync atexit hook — schedules an async shutdown on the running
    loop if one exists, otherwise creates a fresh loop. Best-effort;
    the browser will also die on parent process exit naturally."""
    if _singleton is None or not _singleton.is_alive:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # In-running-loop case — schedule but don't wait.
            asyncio.ensure_future(shutdown_cdp_chrome())
            return
    except RuntimeError:
        pass
    try:
        asyncio.run(shutdown_cdp_chrome())
    except Exception:
        pass


atexit.register(_atexit_handler)
