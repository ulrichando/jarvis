"""JARVIS Evolution Service — standalone daemon for self-improvement.

Runs the evolution cycle periodically:
telemetry → analyze → generate → validate → deploy

Endpoints:
    POST /evolve    — Trigger an evolution cycle
    GET  /status    — Last evolution run status
    GET  /health    — Health check
"""

import asyncio
import signal
import sys
import time
from pathlib import Path

from aiohttp import web

_jarvis_root = Path(__file__).resolve().parent.parent.parent
if str(_jarvis_root) not in sys.path:
    sys.path.insert(0, str(_jarvis_root))

HOST = "127.0.0.1"
PORT = 8704
EVOLUTION_INTERVAL_HOURS = 6


class EvolutionService:
    def __init__(self):
        self._engine = None
        self._last_run = None
        self._last_report = {}
        self._running = False

    async def start(self):
        try:
            from src.evolution.engine import EvolutionEngine
            self._engine = EvolutionEngine()
            print("[JARVIS Evolution] Engine initialized")
        except Exception as e:
            print(f"[JARVIS Evolution] Engine init failed: {e}")

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "service": "jarvis-evolution",
            "last_run": self._last_run,
            "running": self._running,
        })

    async def status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "last_run": self._last_run,
            "last_report": self._last_report,
            "running": self._running,
            "interval_hours": EVOLUTION_INTERVAL_HOURS,
        })

    async def evolve(self, request: web.Request) -> web.Response:
        if self._running:
            return web.json_response({"error": "evolution already running"}, status=409)
        if not self._engine:
            return web.json_response({"error": "engine not available"}, status=503)

        self._running = True
        try:
            report = await self._engine.run_cycle()
            self._last_run = time.time()
            self._last_report = report if isinstance(report, dict) else {"result": str(report)}
            return web.json_response(self._last_report)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
        finally:
            self._running = False

    async def _periodic_evolution(self):
        """Run evolution cycle periodically."""
        while True:
            await asyncio.sleep(EVOLUTION_INTERVAL_HOURS * 3600)
            if self._engine and not self._running:
                self._running = True
                try:
                    report = await self._engine.run_cycle()
                    self._last_run = time.time()
                    self._last_report = report if isinstance(report, dict) else {"result": str(report)}
                    print(f"[JARVIS Evolution] Cycle complete: {self._last_report}")
                except Exception as e:
                    print(f"[JARVIS Evolution] Cycle failed: {e}")
                finally:
                    self._running = False


def main():
    service = EvolutionService()

    app = web.Application()
    app.router.add_get("/health", service.health)
    app.router.add_get("/status", service.status)
    app.router.add_post("/evolve", service.evolve)

    loop = asyncio.new_event_loop()

    def on_shutdown(_sig=None, _frame=None):
        print("[JARVIS Evolution] Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_shutdown)
    signal.signal(signal.SIGINT, on_shutdown)

    loop.run_until_complete(service.start())

    # Start periodic evolution in background
    loop.create_task(service._periodic_evolution())

    print(f"[JARVIS Evolution] Listening on {HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
