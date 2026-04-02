"""Neural Memory Lattice — the core brain structure.

This is JARVIS's artificial hippocampus. All knowledge lives here as an
interconnected graph of memory nodes linked by weighted synapses.

How it works like a real brain:
1. LEARN: New information creates nodes and links them to related memories
2. RECALL: Inverted index lookup + spreading activation (O(1), not O(n))
3. REINFORCE: Accessed memories get stronger (Hebbian learning)
4. FORGET: Unused memories naturally decay and eventually get pruned
5. COMPRESS: Clusters of related memories merge into higher-level concepts
6. RELATE: Synapses carry relationship types (causes, part_of, extends, etc.)
"""

import time
from collections import defaultdict, deque
from brain.memory.lattice.node import MemoryNode, NodeType
from brain.memory.lattice.synapse import Synapse


class NeuralLattice:
    """The neural memory lattice — JARVIS's knowledge graph.

    Now backed by inverted indexes for speed-of-light recall.
    """

    def __init__(self):
        self.nodes: dict[str, MemoryNode] = {}
        self.synapses: dict[tuple[str, str], Synapse] = {}
        # Adjacency lists for fast traversal
        self._outgoing: dict[str, set[str]] = defaultdict(set)
        self._incoming: dict[str, set[str]] = defaultdict(set)
        # Track recently activated nodes for association building
        self._activation_buffer: deque[str] = deque(maxlen=10)
        self._buffer_max = 10
        # Fast index — O(1) recall instead of O(n) scan (lazy import to avoid circular)
        from brain.memory.index import MemoryIndex
        self.index = MemoryIndex()

    # ── LEARN ──────────────────────────────────────────────────────────

    def absorb(
        self,
        content: str,
        node_type: NodeType = NodeType.FACT,
        tags: list[str] | None = None,
        metadata: dict | None = None,
        link_to_recent: bool = True,
    ) -> MemoryNode:
        """Absorb new knowledge into the lattice.

        If the knowledge already exists, reinforce it instead of duplicating.
        Automatically links to recently activated nodes.
        Indexes for O(1) recall.
        """
        node_id = MemoryNode.generate_id(content)

        if node_id in self.nodes:
            # Already know this — reinforce
            existing = self.nodes[node_id]
            existing.activate()
            if link_to_recent:
                self._link_to_buffer(node_id)
            self._push_to_buffer(node_id)
            return existing

        # New knowledge
        node = MemoryNode.create(
            content=content,
            node_type=node_type,
            tags=tags,
            metadata=metadata,
        )
        self.nodes[node.id] = node

        # Index for fast recall
        self.index.index_node(node)

        # Link to recently activated nodes (temporal association)
        if link_to_recent:
            self._link_to_buffer(node.id)

        self._push_to_buffer(node.id)
        return node

    def connect(
        self,
        source_id: str,
        target_id: str,
        weight: float = 0.5,
        context: str = "",
        bidirectional: bool = True,
    ) -> Synapse | None:
        """Create or strengthen a synapse between two nodes."""
        if source_id not in self.nodes or target_id not in self.nodes:
            return None

        key = (source_id, target_id)
        if key in self.synapses:
            self.synapses[key].strengthen()
            if bidirectional:
                reverse = (target_id, source_id)
                if reverse in self.synapses:
                    self.synapses[reverse].strengthen()
            return self.synapses[key]

        synapse = Synapse(
            source_id=source_id,
            target_id=target_id,
            weight=weight,
            context=context,
        )
        self.synapses[key] = synapse
        self._outgoing[source_id].add(target_id)
        self._incoming[target_id].add(source_id)

        if bidirectional:
            reverse_synapse = Synapse(
                source_id=target_id,
                target_id=source_id,
                weight=weight,
                context=context,
            )
            reverse_key = (target_id, source_id)
            self.synapses[reverse_key] = reverse_synapse
            self._outgoing[target_id].add(source_id)
            self._incoming[source_id].add(target_id)

        return synapse

    # ── RECALL ─────────────────────────────────────────────────────────

    def recall(self, query: str, top_k: int = 5) -> list[MemoryNode]:
        """Recall memories related to a query.

        Two-phase recall:
        1. FAST: Inverted index lookup — O(1) per index, finds candidates instantly
        2. SPREAD: Spreading activation through synapses — boosts connected memories

        This is orders of magnitude faster than the old O(n) full scan.
        """
        # Phase 1: Fast index recall
        indexed_results = self.index.recall(query, self.nodes, top_k=top_k * 3)

        scored: dict[str, float] = {}
        for result in indexed_results:
            scored[result.node.id] = result.score

        # Phase 2: Spreading activation — boost neighbors of matched nodes
        activation_spread: dict[str, float] = {}
        for node_id, score in scored.items():
            for neighbor_id in self._outgoing.get(node_id, set()):
                if neighbor_id in scored:
                    continue  # Already directly matched
                synapse_key = (node_id, neighbor_id)
                synapse = self.synapses.get(synapse_key)
                if synapse and synapse.is_alive:
                    neighbor = self.nodes.get(neighbor_id)
                    if neighbor and neighbor.is_alive:
                        spread_score = score * synapse.weight * 0.5
                        activation_spread[neighbor_id] = max(
                            activation_spread.get(neighbor_id, 0),
                            spread_score,
                        )

        # Merge spread scores
        for node_id, spread_score in activation_spread.items():
            scored[node_id] = scored.get(node_id, 0) + spread_score

        # Sort and return top-k
        sorted_ids = sorted(scored, key=lambda x: scored[x], reverse=True)[:top_k]

        results = []
        for node_id in sorted_ids:
            node = self.nodes.get(node_id)
            if node is None:
                continue
            node.activate()
            results.append(node)

        # Update activation buffer
        for node_id in sorted_ids[:3]:
            self._push_to_buffer(node_id)

        return results

    def recall_by_domain(self, domain: str, top_k: int = 10) -> list[MemoryNode]:
        """Instantly recall all memories in a knowledge domain. O(1)."""
        return self.index.recall_by_domain(domain, self.nodes, top_k)

    def recall_by_entity(self, entity: str, top_k: int = 10) -> list[MemoryNode]:
        """Recall all memories about a specific entity. O(1)."""
        return self.index.recall_by_entity(entity, self.nodes, top_k)

    def recall_recent(self, top_k: int = 10) -> list[MemoryNode]:
        """Recall most recently accessed memories. O(k)."""
        return self.index.recall_recent(self.nodes, top_k)

    def recall_by_type(self, node_type: NodeType, top_k: int = 20) -> list[MemoryNode]:
        """Recall all memories of a specific type. O(1)."""
        return self.index.recall_by_type(node_type, self.nodes, top_k)

    def find_knowledge_gaps(self, domain: str) -> dict:
        """Analyze knowledge coverage in a domain."""
        return self.index.find_knowledge_gaps(domain, self.nodes)

    def get_associations(self, node_id: str, min_weight: float = 0.1) -> list[tuple[MemoryNode, float]]:
        """Get all nodes associated with a given node, sorted by synapse weight."""
        if node_id not in self.nodes:
            return []

        associations = []
        for neighbor_id in self._outgoing.get(node_id, set()):
            synapse = self.synapses.get((node_id, neighbor_id))
            neighbor = self.nodes.get(neighbor_id)
            if synapse and neighbor and synapse.weight >= min_weight and neighbor.is_alive:
                associations.append((neighbor, synapse.weight))

        return sorted(associations, key=lambda x: x[1], reverse=True)

    # ── FORGET ─────────────────────────────────────────────────────────

    def decay_all(self, current_time: float | None = None):
        """Apply decay to all nodes and synapses."""
        now = current_time or time.time()

        for node in self.nodes.values():
            node.decay(now)

        for synapse in self.synapses.values():
            synapse.decay(now)

    def prune(self) -> int:
        """Remove dead nodes and synapses. Returns count of pruned items."""
        pruned = 0

        # Prune dead synapses
        dead_synapses = [k for k, s in self.synapses.items() if not s.is_alive]
        for key in dead_synapses:
            source, target = key
            self._outgoing[source].discard(target)
            self._incoming[target].discard(source)
            del self.synapses[key]
            pruned += 1

        # Prune dead nodes (but never prune concept nodes — they're compressed knowledge)
        dead_nodes = [
            nid for nid, n in self.nodes.items()
            if not n.is_alive and n.node_type != NodeType.CONCEPT
        ]
        for node_id in dead_nodes:
            # Remove from index
            self.index.remove_node(node_id)
            # Remove all connected synapses
            for neighbor_id in list(self._outgoing.get(node_id, set())):
                self.synapses.pop((node_id, neighbor_id), None)
                self._incoming[neighbor_id].discard(node_id)
            for neighbor_id in list(self._incoming.get(node_id, set())):
                self.synapses.pop((neighbor_id, node_id), None)
                self._outgoing[neighbor_id].discard(node_id)
            self._outgoing.pop(node_id, None)
            self._incoming.pop(node_id, None)
            del self.nodes[node_id]
            pruned += 1

        return pruned

    # ── COMPRESS ───────────────────────────────────────────────────────

    def compress(self, min_cluster_size: int = 3, min_avg_weight: float = 0.6) -> list[MemoryNode]:
        """Find tightly connected clusters and compress them into concept nodes.

        This is how JARVIS forms intuitions — many related facts merge into
        a single higher-level understanding.
        """
        concepts_created = []
        visited = set()

        for node_id, node in list(self.nodes.items()):
            if node_id in visited or node.node_type == NodeType.CONCEPT:
                continue

            # Find cluster: nodes connected to this one with strong synapses
            cluster = self._find_cluster(node_id, min_avg_weight)
            if len(cluster) < min_cluster_size:
                continue

            # Calculate average synapse weight within cluster
            cluster_weights = []
            for a in cluster:
                for b in cluster:
                    if a != b and (a, b) in self.synapses:
                        cluster_weights.append(self.synapses[(a, b)].weight)

            if not cluster_weights:
                continue

            avg_weight = sum(cluster_weights) / len(cluster_weights)
            if avg_weight < min_avg_weight:
                continue

            # Create concept node
            cluster_contents = [self.nodes[nid].content for nid in cluster if nid in self.nodes]
            concept_summary = f"[CONCEPT: {' | '.join(cluster_contents[:5])}]"

            concept = MemoryNode.create(
                content=concept_summary,
                node_type=NodeType.CONCEPT,
                tags=["auto-compressed"],
                metadata={"cluster_size": len(cluster), "avg_weight": avg_weight},
            )
            concept.children = list(cluster)
            concept.strength = avg_weight  # Concept is as strong as its cluster
            access_counts = [self.nodes[nid].access_count for nid in cluster if nid in self.nodes]
            concept.access_count = max(access_counts) if access_counts else 0

            self.nodes[concept.id] = concept

            # Link concept to all external connections of cluster members
            external_connections: set[str] = set()
            for member_id in cluster:
                for neighbor_id in self._outgoing.get(member_id, set()):
                    if neighbor_id not in cluster:
                        external_connections.add(neighbor_id)

            for ext_id in external_connections:
                self.connect(concept.id, ext_id, weight=0.5, context="compressed-link")

            visited.update(cluster)
            concepts_created.append(concept)

        return concepts_created

    def _find_cluster(self, start_id: str, min_weight: float) -> set[str]:
        """BFS to find a cluster of strongly connected nodes."""
        cluster = {start_id}
        frontier = [start_id]

        while frontier:
            current = frontier.pop(0)
            for neighbor_id in self._outgoing.get(current, set()):
                if neighbor_id in cluster:
                    continue
                synapse = self.synapses.get((current, neighbor_id))
                if synapse and synapse.weight >= min_weight:
                    cluster.add(neighbor_id)
                    frontier.append(neighbor_id)

        return cluster

    # ── INTERNAL ───────────────────────────────────────────────────────

    def _push_to_buffer(self, node_id: str):
        """Add to activation buffer (recent context window)."""
        try:
            self._activation_buffer.remove(node_id)
        except ValueError:
            pass
        self._activation_buffer.append(node_id)

    def _link_to_buffer(self, node_id: str):
        """Link a node to recently activated nodes (temporal association)."""
        for recent_id in self._activation_buffer:
            if recent_id != node_id and recent_id in self.nodes:
                # Recency-weighted: more recent = stronger initial link
                idx = self._activation_buffer.index(recent_id)
                recency = (idx + 1) / len(self._activation_buffer)
                weight = 0.3 * recency
                self.connect(node_id, recent_id, weight=weight, context="temporal")

    # ── STATS ──────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """Get lattice statistics."""
        alive_nodes = sum(1 for n in self.nodes.values() if n.is_alive)
        strong_nodes = sum(1 for n in self.nodes.values() if n.is_strong)
        alive_synapses = sum(1 for s in self.synapses.values() if s.is_alive)
        concepts = sum(1 for n in self.nodes.values() if n.node_type == NodeType.CONCEPT)

        return {
            "total_nodes": len(self.nodes),
            "alive_nodes": alive_nodes,
            "strong_nodes": strong_nodes,
            "total_synapses": len(self.synapses),
            "alive_synapses": alive_synapses,
            "concepts": concepts,
            "buffer_size": len(self._activation_buffer),
            "index": self.index.stats,
        }
