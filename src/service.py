"""JARVIS Brain Service — standalone daemon.

Wraps the full Brain and exposes it over HTTP on 127.0.0.1:8700.
In OS mode, the web server connects to this service instead of
instantiating the brain directly.

Endpoints:
    POST /think       — Process user input {text} → {response}
    POST /learn       — Teach the brain {text} → {result}
    POST /recall      — Recall from knowledge {query} → {results}
    GET  /stats       — Brain statistics
    GET  /health      — Health check
"""

import asyncio
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

_jarvis_root = Path(__file__).resolve().parent.parent
if str(_jarvis_root) not in sys.path:
    sys.path.insert(0, str(_jarvis_root))

from src.brain import Brain

HOST = "127.0.0.1"
PORT = 8700


class BrainService:
    def __init__(self):
        self.brain: Brain | None = None
        self._ready = False

    async def start(self):
        self.brain = Brain(quiet=True)
        self._ready = True
        print(f"[JARVIS Brain] Online — {self.brain.brain_stats()}")

    async def health(self, request: web.Request) -> web.Response:
        if not self._ready:
            return web.json_response({"status": "starting"}, status=503)
        return web.json_response({"status": "ok", "service": "jarvis-brain"})

    async def think(self, request: web.Request) -> web.Response:
        if not self._ready:
            return web.json_response({"error": "brain not ready"}, status=503)

        data = await request.json()
        text = data.get("text", "")
        if not text:
            return web.json_response({"error": "missing text"}, status=400)

        response = await self.brain.think(text)
        return web.json_response({
            "response": response,
            "stats": self.brain.brain_stats(),
        })

    async def learn(self, request: web.Request) -> web.Response:
        if not self._ready:
            return web.json_response({"error": "brain not ready"}, status=503)

        data = await request.json()
        text = data.get("text", "")
        if not text:
            return web.json_response({"error": "missing text"}, status=400)

        result = self.brain.learn(text)
        return web.json_response({"result": result})

    async def recall(self, request: web.Request) -> web.Response:
        if not self._ready:
            return web.json_response({"error": "brain not ready"}, status=503)

        data = await request.json()
        query = data.get("query", "")
        results = self.brain.remember(query)
        return web.json_response({"results": results})

    async def stats(self, request: web.Request) -> web.Response:
        if not self._ready:
            return web.json_response({"status": "starting"}, status=503)
        return web.json_response(self.brain.brain_stats())


def main():
    service = BrainService()

    app = web.Application()
    app.router.add_get("/health", service.health)
    app.router.add_post("/think", service.think)
    app.router.add_post("/learn", service.learn)
    app.router.add_post("/recall", service.recall)
    app.router.add_get("/stats", service.stats)

    loop = asyncio.new_event_loop()

    def on_shutdown(_sig=None, _frame=None):
        print("[JARVIS Brain] Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_shutdown)
    signal.signal(signal.SIGINT, on_shutdown)

    loop.run_until_complete(service.start())

    # Notify systemd
    try:
        notify_socket = os.environ.get("NOTIFY_SOCKET")
        if notify_socket:
            import socket
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.connect(notify_socket)
            sock.sendall(b"READY=1")
            sock.close()
    except Exception:
        pass

    print(f"[JARVIS Brain] Listening on {HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
