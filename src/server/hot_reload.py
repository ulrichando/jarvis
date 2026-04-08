"""
Hot reload for JARVIS development.

Python backend:  watchdog detects .py changes → os.execv() restart
Frontend:        watchdog detects frontend/src changes → npm build → broadcast

Activate with:  JARVIS_HOT_RELOAD=1 python -m src.server.web_server
                JARVIS_HOT_RELOAD=1 jarvis-web
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import threading
from pathlib import Path
from typing import Callable, Awaitable

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

_PROJECT_ROOT = Path(__file__).parent.parent.parent  # jarvis/
_PYTHON_SRC   = _PROJECT_ROOT / "src"
_FRONTEND_SRC = _PROJECT_ROOT / "src" / "server" / "frontend" / "src"
_FRONTEND_DIR = _PROJECT_ROOT / "src" / "server" / "frontend"


# ---------------------------------------------------------------------------
# Debouncing event handler
# ---------------------------------------------------------------------------

class _DebounceHandler(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):
    """Collapses rapid file events into a single callback after a quiet period."""

    def __init__(self, callback: Callable[[list[str]], None], debounce_ms: int = 500):
        if WATCHDOG_AVAILABLE:
            super().__init__()
        self._callback = callback
        self._delay = debounce_ms / 1000.0
        self._timer: threading.Timer | None = None
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def on_modified(self, event: "FileSystemEvent") -> None:  # type: ignore[override]
        if not event.is_directory:
            self._trigger(event.src_path)

    def on_created(self, event: "FileSystemEvent") -> None:  # type: ignore[override]
        if not event.is_directory:
            self._trigger(event.src_path)

    def _trigger(self, path: str) -> None:
        with self._lock:
            self._pending.add(path)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
        self._callback(paths)


# ---------------------------------------------------------------------------
# Main manager
# ---------------------------------------------------------------------------

class HotReloadManager:
    """
    Watches source files and reloads/restarts JARVIS as needed.

    Strategy:
      - Any .py file change  → os.execv() replaces the process (clean restart).
        The existing WS reconnect logic in useWebSocket.js already reloads the
        page on server reconnect for localhost, so no extra signal is needed.
      - Frontend src change  → npm run build, then broadcast {"type":"hot_reload",
        "frontend":true} so connected clients refresh their assets.
    """

    def __init__(self, broadcast_fn: Callable[[dict], Awaitable[None]] | None = None):
        """
        Args:
            broadcast_fn: async callable(dict) — sends a message to all WS clients.
        """
        self._broadcast = broadcast_fn
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer: "Observer | None" = None  # type: ignore[name-defined]
        self._frontend_building = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start file watchers. Must be called after the event loop is running."""
        if not WATCHDOG_AVAILABLE:
            print("[HOT RELOAD] watchdog not installed — run: pip install watchdog")
            print("[HOT RELOAD] Hot reload disabled.")
            return

        self._loop = loop

        py_handler       = _DebounceHandler(self._on_python_change, debounce_ms=400)
        frontend_handler = _DebounceHandler(self._on_frontend_change, debounce_ms=700)

        self._observer = Observer()
        self._observer.schedule(py_handler, str(_PYTHON_SRC), recursive=True)

        if _FRONTEND_SRC.exists():
            self._observer.schedule(frontend_handler, str(_FRONTEND_SRC), recursive=True)

        self._observer.start()
        print(f"[HOT RELOAD] Watching {_PYTHON_SRC.relative_to(_PROJECT_ROOT)}/ "
              f"(Python restart) and "
              f"{_FRONTEND_SRC.relative_to(_PROJECT_ROOT) if _FRONTEND_SRC.exists() else 'N/A'}/ "
              f"(frontend rebuild)")

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=3)

    # ------------------------------------------------------------------
    # Python change → process restart
    # ------------------------------------------------------------------

    def _on_python_change(self, paths: list[str]) -> None:
        """Watchdog thread: .py changed → os.execv() restart."""
        # Ignore frontend files (they're under src/ but handled separately)
        py_paths = [
            p for p in paths
            if p.endswith(".py") and "/frontend/" not in p
        ]
        if not py_paths:
            return

        names = [Path(p).name for p in py_paths]
        print(f"\n[HOT RELOAD] Python changed: {', '.join(names)}")
        print("[HOT RELOAD] Restarting server…")

        # Give the broadcast a moment to flush before we replace the process
        if self._broadcast and self._loop:
            future = asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "hot_reload",
                    "changed": names,
                    "frontend": False,
                    "status": "restarting",
                }),
                self._loop,
            )
            try:
                future.result(timeout=0.5)
            except Exception:
                pass

        time.sleep(0.15)  # let the WS frame go out

        # Replace this process with a fresh copy — preserves env, args, cwd
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ------------------------------------------------------------------
    # Frontend change → npm build + broadcast
    # ------------------------------------------------------------------

    def _on_frontend_change(self, paths: list[str]) -> None:
        """Watchdog thread: frontend src changed → schedule async build."""
        if self._frontend_building:
            return  # already building, skip

        names = [Path(p).name for p in paths]
        print(f"\n[HOT RELOAD] Frontend changed: {', '.join(names)}")

        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._build_frontend(names),
                self._loop,
            )

    async def _build_frontend(self, changed_names: list[str]) -> None:
        if self._frontend_building:
            return
        self._frontend_building = True

        if self._broadcast:
            await self._broadcast({
                "type": "hot_reload",
                "changed": changed_names,
                "frontend": False,
                "status": "building",
            })

        try:
            print("[HOT RELOAD] Running npm run build…")
            proc = await asyncio.create_subprocess_exec(
                "npm", "run", "build",
                cwd=str(_FRONTEND_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            except asyncio.TimeoutError:
                proc.kill()
                print("[HOT RELOAD] Frontend build timed out (>90s)")
                return

            if proc.returncode == 0:
                print("[HOT RELOAD] Frontend rebuilt — broadcasting reload")
                if self._broadcast:
                    await self._broadcast({
                        "type": "hot_reload",
                        "changed": changed_names,
                        "frontend": True,
                        "status": "ready",
                    })
            else:
                err = (stderr or b"").decode(errors="replace")[-600:]
                print(f"[HOT RELOAD] Frontend build failed:\n{err}")
                if self._broadcast:
                    await self._broadcast({
                        "type": "hot_reload",
                        "status": "build_error",
                        "error": err,
                        "changed": changed_names,
                    })

        except Exception as exc:
            print(f"[HOT RELOAD] Build error: {exc}")
        finally:
            self._frontend_building = False
