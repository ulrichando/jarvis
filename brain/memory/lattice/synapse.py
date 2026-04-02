"""Synapses — weighted connections between memory nodes.

Like neural synapses, these encode the *relationships* between memories.
The strength of a synapse determines how strongly two memories are associated.
Co-activation strengthens the link. Disuse weakens it.
"""

import time
from dataclasses import dataclass, field


@dataclass
class Synapse:
    """A weighted connection between two memory nodes."""
    source_id: str
    target_id: str
    weight: float = 0.5             # 0.0 (no association) to 1.0 (inseparable)
    context: str = ""               # Why these are linked: "same conversation", "cause-effect"
    co_activations: int = 0         # How many times both nodes fired together
    created_at: float = field(default_factory=time.time)
    last_activated: float = field(default_factory=time.time)

    @property
    def key(self) -> tuple[str, str]:
        """Unique key for this synapse (directional)."""
        return (self.source_id, self.target_id)

    def strengthen(self, amount: float = 0.1):
        """Strengthen this synapse — Hebbian learning: neurons that fire together wire together."""
        self.co_activations += 1
        self.last_activated = time.time()
        boost = amount * (1.0 - self.weight)  # diminishing returns near 1.0
        self.weight = min(1.0, self.weight + boost)

    def weaken(self, amount: float = 0.05):
        """Weaken this synapse."""
        self.weight = max(0.0, self.weight - amount)

    def decay(self, current_time: float | None = None):
        """Time-based decay for synapses. Unused connections fade."""
        now = current_time or time.time()
        elapsed_hours = (now - self.last_activated) / 3600

        if elapsed_hours <= 0:
            return

        stability = min(8.0, 1.0 + (self.co_activations * 0.3))
        half_life = stability * 48.0  # synapses decay slower than nodes
        decay_factor = 0.5 ** (elapsed_hours / half_life)
        self.weight = max(0.0, self.weight * decay_factor)

    @property
    def is_alive(self) -> bool:
        return self.weight > 0.02

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "weight": self.weight,
            "context": self.context,
            "co_activations": self.co_activations,
            "created_at": self.created_at,
            "last_activated": self.last_activated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Synapse":
        return cls(
            source_id=data["source_id"],
            target_id=data["target_id"],
            weight=data["weight"],
            context=data.get("context", ""),
            co_activations=data["co_activations"],
            created_at=data["created_at"],
            last_activated=data["last_activated"],
        )
