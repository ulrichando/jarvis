"""Memory nodes — the fundamental unit of JARVIS's knowledge."""

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum


class NodeType(Enum):
    """Types of memory nodes."""
    FACT = "fact"           # Static knowledge: "Python is a programming language"
    EPISODIC = "episodic"   # Event memory: "User asked about weather on March 29"
    CONCEPT = "concept"     # Compressed cluster: emergent understanding
    SKILL = "skill"         # Learned capability: "User prefers concise answers"
    ENTITY = "entity"       # Named entity: person, place, tool, project


@dataclass
class MemoryNode:
    """A single unit of memory in the neural lattice.

    Like a neuron — it has activation strength that changes over time.
    Strong memories fire easily. Weak ones fade away.
    """
    id: str
    content: str
    node_type: NodeType
    strength: float = 1.0           # 0.0 (forgotten) to 1.0 (vivid)
    access_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # For concept nodes: IDs of child nodes that were compressed
    children: list[str] = field(default_factory=list)

    @staticmethod
    def generate_id(content: str) -> str:
        """Generate a deterministic ID from content."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @classmethod
    def create(
        cls,
        content: str,
        node_type: NodeType = NodeType.FACT,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> "MemoryNode":
        """Create a new memory node."""
        return cls(
            id=cls.generate_id(content),
            content=content,
            node_type=node_type,
            tags=tags or [],
            metadata=metadata or {},
        )

    def activate(self):
        """Activate this node — strengthens it (like a neuron firing)."""
        self.access_count += 1
        self.last_accessed = time.time()
        # Reinforcement: strength increases with each access, diminishing returns
        boost = 0.1 * (1.0 - self.strength)  # smaller boost as strength approaches 1.0
        self.strength = min(1.0, self.strength + boost)

    def decay(self, current_time: float | None = None):
        """Apply time-based decay to this node's strength.

        Memories decay following a modified Ebbinghaus forgetting curve.
        Frequently accessed memories decay much slower.
        """
        now = current_time or time.time()
        elapsed_hours = (now - self.last_accessed) / 3600

        if elapsed_hours <= 0:
            return

        # Stability factor: more accesses = slower decay
        stability = min(10.0, 1.0 + (self.access_count * 0.5))

        # Decay rate: exponential decay modified by stability
        # Half-life = stability * 24 hours
        half_life = stability * 24.0
        decay_factor = 0.5 ** (elapsed_hours / half_life)

        self.strength = max(0.0, self.strength * decay_factor)

    @property
    def is_alive(self) -> bool:
        """A memory is 'alive' if its strength is above the forget threshold."""
        return self.strength > 0.05

    @property
    def is_strong(self) -> bool:
        """A strong memory (frequently accessed, recently used)."""
        return self.strength > 0.7

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "content": self.content,
            "node_type": self.node_type.value,
            "strength": self.strength,
            "access_count": self.access_count,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "tags": self.tags,
            "metadata": self.metadata,
            "children": self.children,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryNode":
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            content=data["content"],
            node_type=NodeType(data["node_type"]),
            strength=data["strength"],
            access_count=data["access_count"],
            created_at=data["created_at"],
            last_accessed=data["last_accessed"],
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            children=data.get("children", []),
        )
