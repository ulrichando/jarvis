"""Associative Memory Network — brain-inspired memory for Jarvis.

Instead of searching by keywords or vectors, this memory works like
the human brain: memories are CONNECTED by meaning, and recalling one
memory activates related ones through spreading activation.

Key innovations over traditional storage:
1. MULTI-INDEX: Every fact is indexed by subject, relation, object,
   tags, AND associated concepts — so "dog" finds "Rex" and vice versa
2. SPREADING ACTIVATION: Recalling "pets" activates "dog" which
   activates "Rex" — even without direct keyword matches
3. EMOTIONAL WEIGHT: Memories have emotional valence — things learned
   from corrections or frustration are weighted higher
4. TEMPORAL CONTEXT: Facts remember WHEN and WHY they were learned
5. CONFIDENCE DECAY: Unaccessed memories fade; frequently recalled ones
   strengthen (Hebbian learning)
6. CONCEPT CLUSTERS: Related facts form clusters that can be recalled
   as a unit ("everything about the user")

This replaces keyword-matching with associative recall.
"""

from __future__ import annotations

import time
import math
import re
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class MemoryTrace:
    """A single memory trace — a fact with rich metadata.

    Unlike a raw string, a trace knows its structure, its connections,
    and its history. This is the atom of Jarvis's memory.
    """
    content: str                  # The raw text
    subject: str = ""             # Who/what the fact is about
    relation: str = ""            # How subject relates to object
    obj: str = ""                 # The object/value
    tags: set[str] = field(default_factory=set)

    # Temporal context
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0

    # Confidence and importance
    confidence: float = 0.8       # How sure are we this is true
    importance: float = 0.5       # How important is this to the user
    emotional_weight: float = 0.0 # Positive=good memory, negative=correction

    # Source tracking
    source: str = "unknown"       # How was this learned
    context: str = ""             # What was happening when we learned this

    @property
    def strength(self) -> float:
        """Memory strength — combination of recency, frequency, and importance.

        Follows Ebbinghaus forgetting curve modified by access frequency.
        """
        age = time.time() - self.last_accessed + 1.0
        recency = 1.0 / (1.0 + math.log1p(age / 3600))  # Decay over hours
        frequency = math.log1p(self.access_count)
        return (recency * 0.3 + frequency * 0.3 + self.importance * 0.2 +
                self.confidence * 0.2)

    def activate(self):
        """Called when this memory is recalled — strengthens it."""
        self.last_accessed = time.time()
        self.access_count += 1
        # Hebbian: recalled memories get slightly more important
        self.importance = min(1.0, self.importance + 0.02)

    @property
    def keywords(self) -> set[str]:
        """All searchable terms from this memory."""
        words = set()
        for text in [self.content, self.subject, self.obj]:
            words.update(re.findall(r"[a-zA-Z]{3,}", text.lower()))
        words.update(self.tags)
        return words


class AssociativeMemory:
    """Brain-inspired associative memory with spreading activation.

    Three-layer architecture:
    1. TRACE STORE: All memory traces, keyed by ID
    2. INDEXES: Multiple inverted indexes for fast lookup
    3. ASSOCIATION MAP: Bidirectional links between related traces

    Recall works by:
    1. Direct index lookup (O(1)) — finds exact matches
    2. Spreading activation — finds associated memories
    3. Strength ranking — returns strongest matches
    """

    def __init__(self):
        self._traces: dict[str, MemoryTrace] = {}
        self._next_id = 0

        # Multi-index: keyword → set of trace IDs
        self._keyword_index: dict[str, set[str]] = defaultdict(set)
        # Subject index: subject → set of trace IDs
        self._subject_index: dict[str, set[str]] = defaultdict(set)
        # Tag index: tag → set of trace IDs
        self._tag_index: dict[str, set[str]] = defaultdict(set)

        # Association map: trace_id → {related_id: weight}
        self._associations: dict[str, dict[str, float]] = defaultdict(dict)

    @property
    def size(self) -> int:
        return len(self._traces)

    def store(self, trace: MemoryTrace) -> str:
        """Store a memory trace and index it.

        Automatically creates associations with recently stored traces
        and traces that share keywords.
        """
        trace_id = f"m_{self._next_id}"
        self._next_id += 1

        self._traces[trace_id] = trace

        # Index by keywords
        for kw in trace.keywords:
            self._keyword_index[kw].add(trace_id)

        # Index by subject
        if trace.subject:
            self._subject_index[trace.subject.lower()].add(trace_id)

        # Index by tags
        for tag in trace.tags:
            self._tag_index[tag.lower()].add(trace_id)

        # Auto-associate with existing traces that share keywords
        shared_ids: dict[str, int] = defaultdict(int)
        for kw in trace.keywords:
            for other_id in self._keyword_index.get(kw, set()):
                if other_id != trace_id:
                    shared_ids[other_id] += 1

        # Create associations weighted by keyword overlap
        for other_id, shared_count in shared_ids.items():
            other = self._traces.get(other_id)
            if other:
                # Association strength = shared keywords / total keywords
                total = len(trace.keywords | other.keywords)
                weight = shared_count / max(total, 1)
                if weight > 0.1:  # Only associate if meaningful overlap
                    self._associate(trace_id, other_id, weight)

        return trace_id

    def recall(self, query: str, top_k: int = 5,
               prefer_sources: set[str] | None = None) -> list[MemoryTrace]:
        """Recall memories related to a query using spreading activation.

        Phase 1: Direct index lookup — finds traces matching query keywords
        Phase 2: Spreading activation — finds associated traces
        Phase 3: Rank by composite score (match + strength + activation)

        Args:
            prefer_sources: if set, boost traces from these sources (e.g. {"taught", "preference"})
        """
        query_words = set(re.findall(r"[a-zA-Z]{3,}", query.lower()))
        if not query_words:
            return []

        # Phase 1: Direct keyword lookup
        scores: dict[str, float] = defaultdict(float)
        for word in query_words:
            for trace_id in self._keyword_index.get(word, set()):
                scores[trace_id] += 1.0

        # Also check subject index
        for word in query_words:
            for trace_id in self._subject_index.get(word, set()):
                scores[trace_id] += 2.0  # Subject matches are more relevant

        # Phase 2: Spreading activation
        # For each matched trace, spread activation to associated traces
        activation: dict[str, float] = {}
        for trace_id, score in list(scores.items()):
            if score > 0:
                for assoc_id, weight in self._associations.get(trace_id, {}).items():
                    spread = score * weight * 0.5  # Decay factor
                    activation[assoc_id] = max(
                        activation.get(assoc_id, 0), spread
                    )

        # Merge activation into scores
        for trace_id, act_score in activation.items():
            if trace_id not in scores:
                scores[trace_id] = act_score
            else:
                scores[trace_id] += act_score * 0.3

        # Phase 3: Rank by composite score
        if prefer_sources is None:
            prefer_sources = {"taught", "preference", "correction", "personal", "user_taught"}
        ranked = []
        for trace_id, match_score in scores.items():
            trace = self._traces.get(trace_id)
            if not trace:
                continue
            # Composite: match quality × source multiplier
            # Source multiplier amplifies match score — relevant taught facts
            # score higher than relevant seed facts, but a seed fact that's
            # a better keyword match still wins
            source_mult = {
                "taught": 2.5, "correction": 2.0, "preference": 2.0,
                "user_taught": 2.5, "personal": 2.5, "ambient": 0.5,
                "seed": 1.0,
            }.get(trace.source, 1.0)
            if trace.source in prefer_sources:
                source_mult *= 1.5
            composite = match_score * source_mult + trace.strength * 0.3
            ranked.append((composite, trace_id, trace))

        ranked.sort(key=lambda x: x[0], reverse=True)

        # Activate recalled traces (Hebbian learning + STDP timing)
        results = []
        prev_trace = None
        for _, _, trace in ranked[:top_k]:
            trace.activate()
            # STDP: if trace A recalled right before trace B, strengthen A→B
            if prev_trace is not None:
                self._stdp_strengthen(prev_trace, trace)
            prev_trace = trace
            results.append(trace)

        return results

    def recall_about(self, subject: str, top_k: int = 10) -> list[MemoryTrace]:
        """Recall everything known about a subject.

        "Tell me about Ulrich" → finds all facts where subject='ulrich'
        or where 'ulrich' appears in the content.
        """
        subject_lower = subject.lower()
        trace_ids = set()

        # Direct subject lookup
        trace_ids.update(self._subject_index.get(subject_lower, set()))

        # Keyword lookup
        trace_ids.update(self._keyword_index.get(subject_lower, set()))

        results = []
        for tid in trace_ids:
            trace = self._traces.get(tid)
            if trace:
                trace.activate()
                results.append(trace)

        # Sort by strength
        results.sort(key=lambda t: t.strength, reverse=True)
        return results[:top_k]

    def recall_by_tag(self, tag: str, top_k: int = 10) -> list[MemoryTrace]:
        """Recall all memories with a specific tag."""
        results = []
        for tid in self._tag_index.get(tag.lower(), set()):
            trace = self._traces.get(tid)
            if trace:
                results.append(trace)
        results.sort(key=lambda t: t.strength, reverse=True)
        return results[:top_k]

    def forget(self, trace_id: str):
        """Remove a memory trace and all its associations."""
        trace = self._traces.pop(trace_id, None)
        if not trace:
            return

        # Remove from indexes
        for kw in trace.keywords:
            self._keyword_index[kw].discard(trace_id)
        if trace.subject:
            self._subject_index[trace.subject.lower()].discard(trace_id)
        for tag in trace.tags:
            self._tag_index[tag.lower()].discard(trace_id)

        # Remove associations
        for other_id in list(self._associations.get(trace_id, {}).keys()):
            self._associations[other_id].pop(trace_id, None)
        self._associations.pop(trace_id, None)

    def decay_and_prune(self, min_strength: float = 0.05) -> int:
        """Apply decay and prune dead memories. Returns count pruned."""
        dead = [tid for tid, t in self._traces.items()
                if t.strength < min_strength and t.source != "taught"]
        for tid in dead:
            self.forget(tid)
        return len(dead)

    def stats(self) -> dict:
        """Memory statistics."""
        if not self._traces:
            return {"traces": 0, "associations": 0, "indexes": 0}

        return {
            "traces": len(self._traces),
            "associations": sum(len(v) for v in self._associations.values()),
            "keyword_terms": len(self._keyword_index),
            "subjects": len(self._subject_index),
            "tags": len(self._tag_index),
            "avg_strength": sum(t.strength for t in self._traces.values()) / len(self._traces),
        }

    def _stdp_strengthen(self, pre_trace: MemoryTrace, post_trace: MemoryTrace):
        """Spike-Timing Dependent Plasticity — temporal association learning.

        If trace A is recalled right before trace B (within 30s), strengthen A→B.
        This learns SEQUENCES: "after talking about X, Y usually comes up."

        The asymmetric window (A+ > A-) ensures forward associations are stronger
        than backward ones, matching how humans chain thoughts.
        """
        dt = post_trace.last_accessed - pre_trace.last_accessed
        if abs(dt) > 30.0:
            return  # Outside STDP window

        # Find their IDs
        pre_id = post_id = None
        for tid, t in self._traces.items():
            if t is pre_trace:
                pre_id = tid
            elif t is post_trace:
                post_id = tid
            if pre_id and post_id:
                break

        if not pre_id or not post_id:
            return

        if dt > 0:
            # Pre before post → strengthen (A+ = 0.05)
            delta = 0.05 * math.exp(-dt / 10.0)
        else:
            # Post before pre → weaken slightly (A- = -0.02)
            delta = -0.02 * math.exp(dt / 10.0)

        current = self._associations.get(pre_id, {}).get(post_id, 0.0)
        new_weight = max(0.0, min(1.0, current + delta))
        if new_weight > 0.01:
            self._associations[pre_id][post_id] = new_weight

    def _associate(self, id_a: str, id_b: str, weight: float):
        """Create a bidirectional association between two traces."""
        self._associations[id_a][id_b] = max(
            self._associations[id_a].get(id_b, 0), weight
        )
        self._associations[id_b][id_a] = max(
            self._associations[id_b].get(id_a, 0), weight * 0.8
        )
