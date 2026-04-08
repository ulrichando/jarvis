"""JARVIS Memory Store — unified interface to the Neural Memory Lattice.

SQLite handles raw conversation logs (append-only, fast).
The Neural Lattice handles knowledge, associations, and learning.
The Index provides O(1) recall across all dimensions.
"""

import logging
import sqlite3
import time
from src.config import DATA_DIR
from src.memory.lattice import NeuralLattice, MemoryNode, NodeType
from src.memory.lattice.persistence import LatticePersistence
from src.memdir import find_relevant_memories as memdir_search, list_memories as memdir_list

log = logging.getLogger("jarvis.memory")


class MemoryStore:
    """JARVIS's complete memory system.

    Three layers:
    - Conversation log (SQLite): raw transcript, never modified
    - Neural Lattice: living knowledge graph that learns and evolves
    - Memory Index: inverted indexes for speed-of-light recall
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(DATA_DIR / "jarvis.db")

        # Conversation log (SQLite — WAL mode for concurrent access)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

        # Neural Memory Lattice
        self.persistence = LatticePersistence(DATA_DIR / "lattice")
        self.lattice = NeuralLattice()
        self._load_lattice()

        # Optional enhanced memory layers
        self._holographic = None
        self._associative = None
        self._activation = None

        try:
            from src.memory.neural_memory import HolographicMemory
            self._holographic = HolographicMemory()
            from src.memory.common_sense import load_common_sense
            loaded = load_common_sense(self._holographic)
            log.debug("HolographicMemory loaded with %d common-sense facts", loaded)
        except ImportError:
            log.debug("HolographicMemory not available (optional dependency)")
        except (OSError, ValueError, TypeError) as e:
            log.warning("HolographicMemory failed to initialize: %s", e)

        try:
            from src.memory.associative import AssociativeMemory
            self._associative = AssociativeMemory()
        except ImportError:
            log.debug("AssociativeMemory not available (optional dependency)")
        except (OSError, ValueError, TypeError) as e:
            log.warning("AssociativeMemory failed to initialize: %s", e)

        try:
            from src.memory.activation import ActivationMemory
            self._activation = ActivationMemory()
        except ImportError:
            log.debug("ActivationMemory not available (optional dependency)")
        except (OSError, ValueError, TypeError) as e:
            log.warning("ActivationMemory failed to initialize: %s", e)

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_timestamp
                ON conversations(timestamp);
        """)
        self.conn.commit()

    def _load_lattice(self):
        """Load the lattice from disk and rebuild indexes."""
        try:
            nodes, synapses = self.persistence.load()
        except Exception as e:
            log.warning("Failed to load lattice from disk, starting fresh: %s", e)
            return
        if nodes:
            self.lattice.nodes = nodes
            self.lattice.synapses = synapses
            # Rebuild adjacency lists
            for (source, target) in synapses:
                self.lattice._outgoing[source].add(target)
                self.lattice._incoming[target].add(source)
            # Rebuild inverted indexes for fast recall
            self.lattice.index.rebuild(nodes)
            log.debug("Loaded %d nodes and %d synapses from disk", len(nodes), len(synapses))

    # ── Conversation Log ───────────────────────────────────────────────

    def add_turn(self, role: str, content: str):
        """Log a conversation turn. Does NOT absorb into lattice —
        only explicit learn() calls go into the lattice."""
        self.conn.execute(
            "INSERT INTO conversations (role, content, timestamp) VALUES (?, ?, ?)",
            (role, content, time.time()),
        )
        self.conn.commit()

    def mark_session_start(self):
        """Mark the start of a new session.

        Sets the history window to include recent conversations (last 6 hours)
        so JARVIS remembers context across server restarts.
        """
        # Look back 6 hours instead of cutting off at boot time
        self._session_start = time.time() - (6 * 3600)

    def get_history(self, limit: int = 20) -> list[dict]:
        """Get conversation history from the current session window.

        Uses a two-pass approach:
        1. Get the last `limit` entries (recent context)
        2. Also include any entries from the last 3 hours (cross-restart memory)
        Deduplicates and caps at limit * 2 to avoid blowing up context.
        """
        session_start = getattr(self, '_session_start', 0)
        # Time-based window: last 3 hours minimum
        time_cutoff = min(session_start, time.time() - 3 * 3600)
        # Get more entries to cover voice-heavy sessions
        # Voice generates ~150 entries/hour, so 3h = ~450 entries
        # Cap at limit * 4 to stay within LLM context budget
        effective_limit = min(limit * 4, 160)
        rows = self.conn.execute(
            "SELECT role, content, timestamp FROM conversations "
            "WHERE timestamp >= ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (time_cutoff, effective_limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    # ── Neural Lattice Operations ──────────────────────────────────────

    def learn(
        self,
        content: str,
        node_type: NodeType = NodeType.FACT,
        tags: list[str] | None = None,
    ) -> MemoryNode:
        """Learn a new piece of knowledge.

        Stores in the lattice (primary) and also feeds the enhanced memory
        layers so they can improve recall quality.
        """
        node = self.lattice.absorb(content, node_type, tags)

        # Feed holographic memory (FFT-based vector recall)
        if self._holographic:
            try:
                self._holographic.store_text(content, source=str(node_type))
            except (TypeError, ValueError, OSError) as e:
                log.debug("Holographic store failed: %s", e)

        # Feed associative memory (spreading activation network)
        if self._associative:
            try:
                from src.memory.associative import MemoryTrace
                trace = MemoryTrace(
                    content=content,
                    tags=set(tags or []),
                    source="lattice",
                )
                self._associative.store(trace)
            except (TypeError, ValueError, AttributeError) as e:
                log.debug("Associative store failed: %s", e)

        # Feed ACT-R activation memory
        if self._activation:
            try:
                self._activation.store(
                    content=content,
                    tags=set(tags or []),
                    source="lattice",
                )
            except (TypeError, ValueError, AttributeError) as e:
                log.debug("Activation store failed: %s", e)

        return node

    def recall(self, query: str, top_k: int = 5) -> list[MemoryNode]:
        """Recall memories related to a query.

        Merges results from all memory layers:
        1. Neural Lattice (primary — knowledge graph)
        2. HolographicMemory (FFT-based associative recall)
        3. AssociativeMemory (spreading activation network)
        4. ActivationMemory (ACT-R cognitive model)

        The lattice results are authoritative; enhanced layers contribute
        additional memories that the lattice alone might miss.
        """
        lattice_results = self.lattice.recall(query, top_k)
        seen_content = {node.content.lower() for node in lattice_results}

        # Holographic recall — finds associatively similar facts
        if self._holographic:
            try:
                holo_results = self._holographic.recall_text(query, top_k=top_k)
                for result in holo_results:
                    if (result.confidence > 0.4
                            and result.content.lower() not in seen_content):
                        # Wrap as a lightweight MemoryNode so callers get a
                        # uniform type.  Strength mirrors holographic confidence.
                        node = MemoryNode(
                            id=f"holo_{hash(result.content) & 0xFFFFFFFF:08x}",
                            content=result.content,
                            node_type=NodeType.FACT,
                            strength=result.confidence * 0.8,
                        )
                        lattice_results.append(node)
                        seen_content.add(result.content.lower())
            except (TypeError, ValueError, AttributeError) as e:
                log.debug("Holographic recall failed: %s", e)

        # Associative recall — spreading activation finds related traces
        if self._associative:
            try:
                assoc_results = self._associative.recall(query, top_k=top_k)
                for trace in assoc_results:
                    if trace.content.lower() not in seen_content:
                        node = MemoryNode(
                            id=f"assoc_{hash(trace.content) & 0xFFFFFFFF:08x}",
                            content=trace.content,
                            node_type=NodeType.FACT,
                            strength=trace.strength * 0.7,
                            tags=list(trace.tags),
                        )
                        lattice_results.append(node)
                        seen_content.add(trace.content.lower())
            except (TypeError, ValueError, AttributeError) as e:
                log.debug("Associative recall failed: %s", e)

        # ACT-R activation recall — cognitive model with recency/frequency
        if self._activation:
            try:
                act_results = self._activation.recall_by_query(query, top_k=top_k)
                for trace in act_results:
                    if trace.content.lower() not in seen_content:
                        node = MemoryNode(
                            id=f"actr_{hash(trace.content) & 0xFFFFFFFF:08x}",
                            content=trace.content,
                            node_type=NodeType.FACT,
                            strength=0.6,
                            tags=list(trace.tags),
                        )
                        lattice_results.append(node)
                        seen_content.add(trace.content.lower())
            except (TypeError, ValueError, AttributeError) as e:
                log.debug("ACT-R recall failed: %s", e)

        # Sort by strength and return top_k
        lattice_results.sort(key=lambda n: n.strength, reverse=True)
        return lattice_results[:top_k]

    def recall_as_context(self, query: str, top_k: int = 5) -> str:
        """Recall KNOWLEDGE (not conversation history) related to a query.
        Only returns FACTS, SKILLS, CONCEPTS — never episodic/conversation memories.
        Also includes relevant entries from the memdir file-based memory."""
        all_memories = self.lattice.recall(query, top_k * 2)  # Fetch more, then filter

        # Filter: only knowledge, not conversation echoes
        memories = [
            m for m in all_memories
            if m.node_type in (NodeType.FACT, NodeType.SKILL, NodeType.CONCEPT, NodeType.ENTITY)
            and m.strength > 0.3
        ][:top_k]

        lines = []
        if memories:
            lines.append("[Known facts:]")
            for mem in memories:
                lines.append(f"  - {mem.content}")

        # Also search memdir for file-based memories
        try:
            memdir_results = memdir_search(query, max_results=min(3, top_k))
            if memdir_results:
                lines.append("[Memory files:]")
                for entry in memdir_results:
                    preview = entry.content[:150].replace("\n", " ")
                    lines.append(f"  - [{entry.id}] {preview}")
        except Exception:
            pass

        return "\n".join(lines) if lines else ""

    # ── Fast recall methods (powered by inverted index) ───────────────

    def recall_domain(self, domain: str, top_k: int = 10) -> list[MemoryNode]:
        """Instantly recall all knowledge in a domain (security, coding, personal, etc.)."""
        return self.lattice.recall_by_domain(domain, top_k)

    def recall_entity(self, entity: str, top_k: int = 10) -> list[MemoryNode]:
        """Recall everything JARVIS knows about a specific entity."""
        return self.lattice.recall_by_entity(entity, top_k)

    def recall_recent(self, top_k: int = 10) -> list[MemoryNode]:
        """What was JARVIS just thinking about?"""
        return self.lattice.recall_recent(top_k)

    def recall_skills(self, top_k: int = 20) -> list[MemoryNode]:
        """Recall all learned skills."""
        return self.lattice.recall_by_type(NodeType.SKILL, top_k)

    def knowledge_gaps(self, domain: str) -> dict:
        """Analyze what JARVIS knows vs doesn't know in a domain."""
        return self.lattice.find_knowledge_gaps(domain)

    def get_associations(self, query: str) -> list[tuple[MemoryNode, float]]:
        """Get associations for the best matching memory."""
        results = self.lattice.recall(query, top_k=1)
        if results:
            return self.lattice.get_associations(results[0].id)
        return []

    # ── Maintenance ────────────────────────────────────────────────────

    def save(self):
        """Persist the lattice to disk."""
        try:
            self.persistence.save(self.lattice.nodes, self.lattice.synapses)
        except Exception as e:
            log.error("Failed to save lattice to disk: %s", e)

    def maintain(self):
        """Run maintenance: decay, prune, compress, rebuild index, save."""
        self.lattice.decay_all()
        pruned = self.lattice.prune()
        concepts = self.lattice.compress()
        # Rebuild index after maintenance (pruning changes the node set)
        self.lattice.index.rebuild(self.lattice.nodes)
        self.save()

        # Maintain enhanced memory layers
        assoc_pruned = 0
        if self._holographic:
            try:
                self._holographic.decay()
            except Exception:
                pass
        if self._associative:
            try:
                assoc_pruned = self._associative.decay_and_prune()
            except Exception:
                pass

        return {
            "pruned": pruned,
            "assoc_pruned": assoc_pruned,
            "new_concepts": len(concepts),
            "stats": self.lattice.stats,
        }

    @property
    def stats(self) -> dict:
        """Get memory system stats."""
        conv_count = self.conn.execute(
            "SELECT COUNT(*) FROM conversations"
        ).fetchone()[0]
        result = {
            "conversations": conv_count,
            "lattice": self.lattice.stats,
            "disk_size": self.persistence.file_size_human,
        }
        if self._holographic:
            try:
                result["holographic"] = self._holographic.stats()
            except Exception:
                pass
        if self._associative:
            try:
                result["associative"] = self._associative.stats()
            except Exception:
                pass
        if self._activation:
            try:
                result["activation"] = self._activation.stats()
            except Exception:
                pass
        return result

    def close(self):
        """Shut down the memory system."""
        self.save()
        self.conn.close()
