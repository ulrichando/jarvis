"""Lattice persistence — save/load the neural memory to disk.

Uses MessagePack for compact binary serialization (much smaller than JSON).
Falls back to JSON if msgpack isn't available.
"""

import json
import time
from pathlib import Path
from brain.memory.lattice.node import MemoryNode
from brain.memory.lattice.synapse import Synapse

try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False


class LatticePersistence:
    """Handles saving and loading the neural lattice to/from disk."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lattice_file = data_dir / ("lattice.msgpack" if HAS_MSGPACK else "lattice.json")
        self.backup_dir = data_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)

    def save(self, nodes: dict[str, MemoryNode], synapses: dict) -> Path:
        """Serialize and save the entire lattice to disk."""
        data = {
            "version": 1,
            "saved_at": time.time(),
            "nodes": {nid: node.to_dict() for nid, node in nodes.items()},
            "synapses": {
                f"{k[0]}:{k[1]}": syn.to_dict()
                for k, syn in synapses.items()
            },
        }

        # Create backup of existing file
        if self.lattice_file.exists():
            backup_name = f"lattice_{int(time.time())}.bak"
            backup_path = self.backup_dir / backup_name
            self.lattice_file.rename(backup_path)
            self._cleanup_backups(keep=5)

        if HAS_MSGPACK:
            with open(self.lattice_file, "wb") as f:
                msgpack.pack(data, f)
        else:
            with open(self.lattice_file, "w") as f:
                json.dump(data, f, separators=(",", ":"))

        return self.lattice_file

    def load(self) -> tuple[dict[str, MemoryNode], dict[tuple[str, str], Synapse]]:
        """Load the lattice from disk. Returns (nodes, synapses)."""
        if not self.lattice_file.exists():
            return {}, {}

        if HAS_MSGPACK:
            with open(self.lattice_file, "rb") as f:
                data = msgpack.unpack(f, raw=False)
        else:
            with open(self.lattice_file, "r") as f:
                data = json.load(f)

        nodes = {}
        for nid, node_data in data.get("nodes", {}).items():
            nodes[nid] = MemoryNode.from_dict(node_data)

        synapses = {}
        for key_str, syn_data in data.get("synapses", {}).items():
            source, target = key_str.split(":", 1)
            synapse = Synapse.from_dict(syn_data)
            synapses[(source, target)] = synapse

        return nodes, synapses

    def _cleanup_backups(self, keep: int = 5):
        """Keep only the N most recent backups."""
        backups = sorted(self.backup_dir.glob("lattice_*.bak"))
        for old_backup in backups[:-keep]:
            old_backup.unlink()

    @property
    def file_size(self) -> int:
        """Size of the lattice file in bytes."""
        if self.lattice_file.exists():
            return self.lattice_file.stat().st_size
        return 0

    @property
    def file_size_human(self) -> str:
        """Human-readable file size."""
        size = self.file_size
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
