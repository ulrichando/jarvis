"""
Entrypoint classes for JARVIS.

Each entrypoint handles setup(), run(), and shutdown() for a specific mode.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.entrypoints")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jarvis_home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class Entrypoint(abc.ABC):
    """Base class for all JARVIS entrypoints."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}
        self.brain: Any = None  # set during setup
        self._running = False

    @abc.abstractmethod
    async def setup(self) -> None:
        """Initialise subsystems for this entrypoint."""

    @abc.abstractmethod
    async def run(self) -> None:
        """Main loop / server."""

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Graceful teardown."""

    # -- shared helpers -----------------------------------------------------

    async def _load_config(self) -> dict[str, Any]:
        """Load global config from JARVIS_HOME."""
        config_path = _jarvis_home() / "config.json"
        if config_path.exists():
            try:
                self.config.update(json.loads(config_path.read_text()))
            except Exception as exc:
                log.warning("Failed to load config.json: %s", exc)
        return self.config

    async def _init_brain(self, **overrides: Any) -> Any:
        """Instantiate the Brain and store it on self."""
        from src.brain import Brain  # deferred to avoid circular imports

        merged = {**self.config, **overrides}
        self.brain = Brain(**{k: v for k, v in merged.items() if k in Brain.__init__.__code__.co_varnames})
        return self.brain

    async def _register_signals(self) -> None:
        """Install SIGINT/SIGTERM handlers that trigger shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._signal_handler(s)))

    async def _signal_handler(self, sig: signal.Signals) -> None:
        log.info("Received signal %s, shutting down", sig.name)
        await self.shutdown()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class CLIEntrypoint(Entrypoint):
    """Interactive terminal REPL."""

    async def setup(self) -> None:
        await self._load_config()

        # Check first-run
        from src.setup_wizard import check_setup_needed, run_setup

        if check_setup_needed():
            cfg = run_setup()
            self.config.update(cfg)
            # Reload after writing
            await self._load_config()

        await self._init_brain(mode="cli")
        await self._register_signals()
        log.info("CLI entrypoint setup complete")

    async def run(self) -> None:
        self._running = True

        # Defer to the existing CLI runner
        try:
            from src.cli.jarvis_cli import main as cli_main  # type: ignore[import-untyped]
            await cli_main()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._running = False

    async def shutdown(self) -> None:
        self._running = False
        if self.brain:
            try:
                await self.brain.shutdown()
            except Exception:
                pass
        log.info("CLI entrypoint shut down")


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------

class WebEntrypoint(Entrypoint):
    """HTTP + WebSocket server."""

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(config)
        self.host: str = kwargs.get("host", "0.0.0.0")
        self.port: int = kwargs.get("port", 8765)
        self._server: Any = None

    async def setup(self) -> None:
        await self._load_config()
        await self._init_brain(mode="web")
        await self._register_signals()
        log.info("Web entrypoint setup complete (port %d)", self.port)

    async def run(self) -> None:
        self._running = True
        try:
            from src.server.web_server import create_app, run_server  # type: ignore[import-untyped]
            app = await create_app(brain=self.brain)
            await run_server(app, host=self.host, port=self.port)
        except Exception:
            log.exception("Web server error")
        finally:
            self._running = False

    async def shutdown(self) -> None:
        self._running = False
        if self.brain:
            try:
                await self.brain.shutdown()
            except Exception:
                pass
        log.info("Web entrypoint shut down")


# ---------------------------------------------------------------------------
# Desktop
# ---------------------------------------------------------------------------

class DesktopEntrypoint(Entrypoint):
    """Tauri desktop overlay launcher."""

    async def setup(self) -> None:
        await self._load_config()
        await self._init_brain(mode="desktop")
        log.info("Desktop entrypoint setup complete")

    async def run(self) -> None:
        self._running = True
        try:
            import subprocess, os
            jarvis_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tauri_bin = os.path.join(jarvis_root, "src", "desktop-tauri", "src-tauri", "target", "debug", "jarvis-desktop")
            proc = subprocess.Popen(
                [tauri_bin],
                cwd=jarvis_root,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")},
            )
            proc.wait()
        except Exception:
            log.exception("Desktop app error")
        finally:
            self._running = False

    async def shutdown(self) -> None:
        self._running = False
        if self.brain:
            try:
                await self.brain.shutdown()
            except Exception:
                pass
        log.info("Desktop entrypoint shut down")


# ---------------------------------------------------------------------------
# API (headless)
# ---------------------------------------------------------------------------

class APIEntrypoint(Entrypoint):
    """Headless API mode -- no interactive UI."""

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(config)
        self.host: str = kwargs.get("host", "0.0.0.0")
        self.port: int = kwargs.get("port", 8766)

    async def setup(self) -> None:
        await self._load_config()
        await self._init_brain(mode="api")
        await self._register_signals()
        log.info("API entrypoint setup complete (port %d)", self.port)

    async def run(self) -> None:
        self._running = True
        try:
            from aiohttp import web  # type: ignore[import-untyped]

            app = web.Application()
            # Minimal health endpoint; the real API surface is added by
            # src.server.web_server or a dedicated API module.
            app.router.add_get("/health", self._health_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
            log.info("API server listening on %s:%d", self.host, self.port)

            # Block until shutdown
            while self._running:
                await asyncio.sleep(1)

            await runner.cleanup()
        except Exception:
            log.exception("API server error")
        finally:
            self._running = False

    async def _health_handler(self, request: Any) -> Any:
        from aiohttp import web  # type: ignore[import-untyped]
        status = "ready" if self.brain else "starting"
        return web.json_response({"status": status})

    async def shutdown(self) -> None:
        self._running = False
        if self.brain:
            try:
                await self.brain.shutdown()
            except Exception:
                pass
        log.info("API entrypoint shut down")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_ENTRYPOINTS: dict[str, type[Entrypoint]] = {
    "cli": CLIEntrypoint,
    "web": WebEntrypoint,
    "desktop": DesktopEntrypoint,
    "api": APIEntrypoint,
}


def get_entrypoint(mode: str, **kwargs: Any) -> Entrypoint:
    """Factory: return the appropriate Entrypoint for *mode*.

    Raises ValueError for unknown modes.
    """
    cls = _ENTRYPOINTS.get(mode)
    if cls is None:
        raise ValueError(
            f"Unknown entrypoint mode '{mode}'. "
            f"Valid modes: {', '.join(_ENTRYPOINTS)}"
        )
    return cls(**kwargs)
