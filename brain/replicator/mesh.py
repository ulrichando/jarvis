"""JARVIS Mesh Network — multiple JARVIS instances share knowledge.

When JARVIS is deployed to multiple devices, they can:
- Discover each other on the network
- Share learned facts and skills
- Coordinate tasks across devices
- Report status to the master (Ulrich's main machine)

Protocol: simple HTTP API between JARVIS instances.
Each instance exposes /api/mesh/* endpoints.
"""

import json
import requests
from dataclasses import dataclass, field
from brain.replicator.scanner import quick_scan


@dataclass
class MeshNode:
    ip: str
    port: int = 8765
    name: str = ""
    last_seen: float = 0
    status: str = "unknown"  # online, offline, busy
    memories: int = 0


class MeshNetwork:
    """Manages the JARVIS mesh — discovery, sync, coordination."""

    def __init__(self, my_ip: str = ""):
        self.nodes: dict[str, MeshNode] = {}
        self.my_ip = my_ip or self._get_my_ip()

    def discover(self) -> list[MeshNode]:
        """Scan the network for other JARVIS instances."""
        targets = quick_scan()
        found = []

        for target in targets:
            if target.ip == self.my_ip:
                continue

            # Check if JARVIS is running on port 8765
            try:
                r = requests.get(f"http://{target.ip}:8765/api/mesh/ping", timeout=2)
                if r.status_code == 200:
                    data = r.json()
                    node = MeshNode(
                        ip=target.ip,
                        name=data.get("name", target.ip),
                        last_seen=time.time(),
                        status="online",
                        memories=data.get("memories", 0),
                    )
                    self.nodes[target.ip] = node
                    found.append(node)
            except Exception:
                continue

        return found

    def sync_knowledge(self, target_ip: str) -> dict:
        """Push our knowledge to another JARVIS instance."""
        try:
            # Get our facts
            from brain.memory.store import MemoryStore
            from brain.memory.lattice.node import NodeType
            store = MemoryStore()
            facts = []
            for nid, node in store.lattice.nodes.items():
                if node.node_type in (NodeType.FACT, NodeType.SKILL) and node.is_alive:
                    facts.append({"content": node.content, "type": node.node_type.value})

            if not facts:
                return {"synced": 0, "message": "Nothing to sync."}

            r = requests.post(
                f"http://{target_ip}:8765/api/mesh/learn",
                json={"facts": facts},
                timeout=10,
            )

            if r.status_code == 200:
                return {"synced": len(facts), "message": f"Synced {len(facts)} facts to {target_ip}."}
            return {"synced": 0, "error": r.text}

        except Exception as e:
            return {"synced": 0, "error": str(e)}

    def pull_knowledge(self, target_ip: str) -> dict:
        """Pull knowledge from another JARVIS instance."""
        try:
            r = requests.get(f"http://{target_ip}:8765/api/mesh/knowledge", timeout=10)
            if r.status_code == 200:
                data = r.json()
                facts = data.get("facts", [])

                # Store locally
                from brain.memory.store import MemoryStore
                from brain.memory.lattice.node import NodeType
                store = MemoryStore()
                learned = 0
                for fact in facts:
                    nt = NodeType.FACT if fact.get("type") == "fact" else NodeType.SKILL
                    store.learn(fact["content"], nt, ["mesh-synced"])
                    learned += 1

                return {"learned": learned, "message": f"Learned {learned} facts from {target_ip}."}
            return {"learned": 0, "error": r.text}

        except Exception as e:
            return {"learned": 0, "error": str(e)}

    def broadcast_task(self, task: str) -> list[dict]:
        """Send a task to all mesh nodes and collect results."""
        results = []
        for ip, node in self.nodes.items():
            if node.status != "online":
                continue
            try:
                r = requests.post(
                    f"http://{ip}:8765/api/mesh/task",
                    json={"task": task},
                    timeout=30,
                )
                if r.status_code == 200:
                    results.append({"ip": ip, "result": r.json().get("result", "")})
            except Exception:
                results.append({"ip": ip, "result": "unreachable"})
        return results

    def get_status(self) -> list[dict]:
        return [
            {"ip": n.ip, "name": n.name, "status": n.status,
             "memories": n.memories, "last_seen": n.last_seen}
            for n in self.nodes.values()
        ]

    @staticmethod
    def _get_my_ip() -> str:
        import subprocess
        try:
            return subprocess.run("hostname -I", shell=True, capture_output=True, text=True).stdout.strip().split()[0]
        except Exception:
            return "127.0.0.1"
