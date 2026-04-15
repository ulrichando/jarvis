"""JARVIS Memory Service — standalone daemon for the NeuralLattice memory system.

Exposes the MemoryStore over HTTP on 127.0.0.1:8701 so other JARVIS
subsystems can access memory independently.

Endpoints:
    POST /learn          — Learn a new fact {content, node_type?, tags?}
    POST /recall         — Recall memories {query, top_k?}
    POST /recall_context — Recall as context string {query, top_k?}
    POST /add_turn       — Log conversation turn {role, content}
    GET  /history        — Get conversation history {limit?}
    GET  /stats          — Memory system stats
    POST /maintain       — Run maintenance cycle
    GET  /health         — Health check
"""

import asyncio
import signal
import sys
from pathlib import Path

from aiohttp import web

# Ensure jarvis root is on path
_jarvis_root = Path(__file__).resolve().parent.parent.parent
if str(_jarvis_root) not in sys.path:
    sys.path.insert(0, str(_jarvis_root))

from src.memory.store import MemoryStore
from src.memory.store import NodeType


NODE_TYPE_MAP = {
    "fact": NodeType.FACT,
    "concept": NodeType.CONCEPT,
    "skill": NodeType.SKILL,
    "episodic": NodeType.EPISODIC,
    "entity": NodeType.ENTITY,
}

HOST = "127.0.0.1"
PORT = 8701


class MemoryService:
    def __init__(self):
        self.store: MemoryStore | None = None

    async def start(self):
        self.store = MemoryStore()
        print(f"[JARVIS Memory] Lattice loaded — {self.store.stats}")

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "jarvis-memory"})

    async def stats(self, request: web.Request) -> web.Response:
        return web.json_response(self.store.stats)

    async def learn(self, request: web.Request) -> web.Response:
        data = await request.json()
        content = data.get("content", "")
        if not content:
            return web.json_response({"error": "missing content"}, status=400)

        node_type_str = data.get("node_type", "fact").lower()
        node_type = NODE_TYPE_MAP.get(node_type_str, NodeType.FACT)
        tags = data.get("tags")

        node = self.store.learn(content, node_type, tags)
        return web.json_response({
            "id": node.id,
            "content": node.content,
            "type": node.node_type.name,
        })

    async def recall(self, request: web.Request) -> web.Response:
        data = await request.json()
        query = data.get("query", "")
        top_k = data.get("top_k", 5)

        nodes = self.store.recall(query, top_k)
        return web.json_response({
            "results": [
                {"id": n.id, "content": n.content, "type": n.node_type.name, "strength": n.strength}
                for n in nodes
            ]
        })

    async def recall_context(self, request: web.Request) -> web.Response:
        data = await request.json()
        query = data.get("query", "")
        top_k = data.get("top_k", 5)

        context = self.store.recall_as_context(query, top_k)
        return web.json_response({"context": context})

    async def add_turn(self, request: web.Request) -> web.Response:
        data = await request.json()
        role = data.get("role", "")
        content = data.get("content", "")
        if not role or not content:
            return web.json_response({"error": "missing role or content"}, status=400)

        self.store.add_turn(role, content)
        return web.json_response({"status": "ok"})

    async def history(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", "20"))
        entries = self.store.get_history(limit)
        return web.json_response({"history": entries})

    async def maintain(self, request: web.Request) -> web.Response:
        result = self.store.maintain()
        return web.json_response(result)

    def shutdown(self):
        if self.store:
            print("[JARVIS Memory] Saving lattice...")
            self.store.close()
            print("[JARVIS Memory] Shutdown complete.")


def main():
    service = MemoryService()

    app = web.Application()
    app.router.add_get("/health", service.health)
    app.router.add_get("/stats", service.stats)
    app.router.add_post("/learn", service.learn)
    app.router.add_post("/recall", service.recall)
    app.router.add_post("/recall_context", service.recall_context)
    app.router.add_post("/add_turn", service.add_turn)
    app.router.add_get("/history", service.history)
    app.router.add_post("/maintain", service.maintain)

    loop = asyncio.new_event_loop()

    def on_shutdown(_sig=None, _frame=None):
        service.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_shutdown)
    signal.signal(signal.SIGINT, on_shutdown)

    loop.run_until_complete(service.start())

    # Notify systemd we're ready (if running under systemd)
    try:
        import socket
        notify_socket = os.environ.get("NOTIFY_SOCKET")
        if notify_socket:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.connect(notify_socket)
            sock.sendall(b"READY=1")
            sock.close()
    except Exception:
        pass

    print(f"[JARVIS Memory] Listening on {HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    import os
    main()
