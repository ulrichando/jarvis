"""ACT-R Activation-Based Memory Retrieval.

Inspired by Anderson's ACT-R cognitive architecture (Carnegie Mellon).
Instead of searching for keywords, memories have ACTIVATION LEVELS that
determine how quickly and accurately they can be recalled.

The activation equation (from ACT-R):
    A_i = B_i + Σ(W_j × S_ji) + ε

Where:
    A_i = total activation of memory i
    B_i = base-level activation (recency + frequency)
    W_j = attentional weight of context element j
    S_ji = association strength between context j and memory i
    ε = noise (stochastic variation)

The base-level activation follows the power law of forgetting:
    B_i = ln(Σ t_k^(-d))

Where:
    t_k = time since the k-th access
    d = decay rate (typically 0.5)

This means:
- Recently accessed memories are more active (recency)
- Frequently accessed memories are more active (frequency)
- Memories related to current context get a boost (spreading activation)
- There's random variation (like real human memory)

This is MATHEMATICALLY OPTIMAL for memory retrieval under uncertainty
(Anderson showed this corresponds to Bayesian inference about memory need).
"""

from __future__ import annotations

import math
import time
import random
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class ActivationTrace:
    """A memory with ACT-R style activation tracking."""
    content: str
    subject: str = ""
    relation: str = ""
    obj: str = ""

    # Access history — timestamps of every recall
    access_times: list[float] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    # Association strengths to other concepts
    associations: dict[str, float] = field(default_factory=dict)

    # Tags for categorical retrieval
    tags: set[str] = field(default_factory=set)
    source: str = "unknown"

    # Decay parameter (0.5 = standard ACT-R)
    decay: float = 0.5

    # Retrieval threshold — below this, memory is "forgotten"
    threshold: float = -1.0

    def record_access(self):
        """Record that this memory was accessed (recalled or stored)."""
        self.access_times.append(time.time())
        # Keep only last 100 access times to save memory
        if len(self.access_times) > 100:
            self.access_times = self.access_times[-100:]

    @property
    def base_level_activation(self) -> float:
        """B_i = ln(Σ t_k^(-d)) — the ACT-R base-level learning equation.

        Combines recency and frequency into a single number.
        Higher = more easily recalled.
        """
        now = time.time()
        if not self.access_times:
            # Never accessed — use creation time
            age = max(now - self.created_at, 1.0)
            return math.log(age ** (-self.decay))

        total = 0.0
        for t in self.access_times:
            age = max(now - t, 0.01)  # Avoid division by zero
            total += age ** (-self.decay)

        if total <= 0:
            return self.threshold - 1  # Below threshold
        return math.log(total)

    def spreading_activation(self, context_concepts: dict[str, float]) -> float:
        """Σ(W_j × S_ji) — activation spread from current context.

        context_concepts: {concept: attentional_weight}
        """
        total = 0.0
        for concept, weight in context_concepts.items():
            assoc = self.associations.get(concept, 0.0)
            total += weight * assoc
        return total

    def total_activation(self, context: dict[str, float] | None = None,
                         noise: float = 0.1) -> float:
        """A_i = B_i + spreading + noise — total activation level.

        This determines if and how quickly this memory can be recalled.
        """
        base = self.base_level_activation
        spread = self.spreading_activation(context or {})
        epsilon = random.gauss(0, noise) if noise > 0 else 0.0
        return base + spread + epsilon

    @property
    def can_be_recalled(self) -> bool:
        """Is this memory above the retrieval threshold?"""
        return self.total_activation(noise=0) > self.threshold

    @property
    def retrieval_time_ms(self) -> float:
        """Simulated retrieval time — higher activation = faster recall.

        RT = F × e^(-f×A_i) — the ACT-R retrieval time equation.
        F = latency factor, f = latency exponent.
        """
        activation = self.total_activation(noise=0)
        F = 1.0   # Base latency (seconds)
        f = 1.0   # Exponent
        rt = F * math.exp(-f * activation)
        return min(rt * 1000, 5000)  # Cap at 5 seconds, convert to ms


class ActivationMemory:
    """ACT-R style memory system with activation-based retrieval.

    Instead of searching by keywords (O(n)), retrieval works by:
    1. Compute activation for ALL memories in parallel (vectorized)
    2. Filter by threshold (forgotten memories don't compete)
    3. Return the highest-activation memories

    This naturally handles:
    - Recency: recent memories have higher base activation
    - Frequency: often-recalled memories are easier to recall
    - Context: related concepts boost activation (priming)
    - Forgetting: old, unused memories fall below threshold
    """

    def __init__(self, retrieval_threshold: float = -1.0,
                 decay_rate: float = 0.5):
        self._traces: dict[str, ActivationTrace] = {}
        self._concept_index: dict[str, set[str]] = defaultdict(set)
        self.threshold = retrieval_threshold
        self.decay = decay_rate
        self._next_id = 0

    def store(self, content: str, subject: str = "", relation: str = "",
              obj: str = "", tags: set[str] | None = None,
              source: str = "unknown") -> str:
        """Store a memory and create associations."""
        trace_id = f"act_{self._next_id}"
        self._next_id += 1

        trace = ActivationTrace(
            content=content, subject=subject, relation=relation, obj=obj,
            tags=tags or set(), source=source, decay=self.decay,
            threshold=self.threshold,
        )
        trace.record_access()  # Initial encoding counts as an access

        self._traces[trace_id] = trace

        # Index by concepts for spreading activation
        for concept in self._extract_concepts(content, subject, obj):
            self._concept_index[concept].add(trace_id)

        # Create associations between this memory and others sharing concepts
        concepts = self._extract_concepts(content, subject, obj)
        for concept in concepts:
            for other_id in self._concept_index.get(concept, set()):
                if other_id != trace_id:
                    other = self._traces.get(other_id)
                    if other:
                        # Bidirectional association
                        trace.associations[concept] = trace.associations.get(concept, 0) + 0.2
                        other.associations[concept] = other.associations.get(concept, 0) + 0.1

        return trace_id

    def recall(self, context: dict[str, float] | None = None,
               top_k: int = 5, noise: float = 0.05) -> list[ActivationTrace]:
        """Retrieve memories by activation level.

        context: {concept: attentional_weight} — what's currently in focus.
        Returns the top-k most activated memories above threshold.
        """
        if not self._traces:
            return []

        # Compute activation for all memories
        scored = []
        for trace_id, trace in self._traces.items():
            activation = trace.total_activation(context=context, noise=noise)
            if activation > trace.threshold:
                scored.append((activation, trace_id, trace))

        # Sort by activation (highest first)
        scored.sort(key=lambda x: x[0], reverse=True)

        # Return top-k and record access
        results = []
        for _, _, trace in scored[:top_k]:
            trace.record_access()
            results.append(trace)

        return results

    def recall_by_query(self, query: str, top_k: int = 5) -> list[ActivationTrace]:
        """Convert a text query into context weights and retrieve.

        Each word in the query becomes a context concept with equal weight.
        """
        words = set(query.lower().split())
        stop = {"what", "is", "the", "a", "an", "of", "who", "where", "when",
                "how", "does", "do", "can", "my", "your", "i", "you", "tell",
                "me", "about", "have", "any", "are", "for"}
        concepts = {w: 1.0 for w in words if w not in stop and len(w) > 2}

        # Boost user-related concepts for personal queries
        if any(w in query.lower() for w in ["my ", "i ", "me "]):
            concepts["user"] = 1.5

        return self.recall(context=concepts, top_k=top_k)

    def get_activation_profile(self) -> dict[str, float]:
        """Get activation levels for all memories — for visualization."""
        return {
            tid: trace.base_level_activation
            for tid, trace in self._traces.items()
        }

    @property
    def size(self) -> int:
        return len(self._traces)

    def stats(self) -> dict:
        if not self._traces:
            return {"size": 0}
        activations = [t.base_level_activation for t in self._traces.values()]
        return {
            "size": len(self._traces),
            "concepts_indexed": len(self._concept_index),
            "avg_activation": sum(activations) / len(activations),
            "max_activation": max(activations),
            "recallable": sum(1 for a in activations if a > self.threshold),
            "forgotten": sum(1 for a in activations if a <= self.threshold),
        }

    @staticmethod
    def _extract_concepts(content: str, subject: str, obj: str) -> set[str]:
        """Extract concept tokens for indexing and association."""
        import re
        words = set()
        for text in [content, subject, obj]:
            words.update(re.findall(r"[a-z]{3,}", text.lower()))
        return words
