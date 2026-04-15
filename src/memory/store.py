"""JARVIS Memory Store — PostgreSQL + Weaviate semantic memory.

Short-term (conversation log): PostgreSQL (mandatory).
Long-term (semantic search):   Weaviate (vector database).
"""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from src.memdir import find_relevant_memories as memdir_search, list_memories as memdir_list

log = logging.getLogger("jarvis.memory")


# ── Simple MemoryNode and NodeType for compatibility ──────────────────
class NodeType(str, Enum):
    """Memory node classification."""
    FACT = "fact"
    SKILL = "skill"
    CONCEPT = "concept"
    ENTITY = "entity"
    GOAL = "goal"
    EPISODIC = "episodic"


@dataclass
class MemoryNode:
    """A memory item."""
    id: str
    content: str
    node_type: NodeType
    strength: float = 1.0
    tags: list[str] | None = None
    timestamp: float | None = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class MemoryStore:
    """JARVIS's memory system.

    Two layers:
    - Conversation log: PostgreSQL (short-term, mandatory)
    - Semantic memory: Weaviate (long-term, vector-based)
    """

    def __init__(self, db_path: str | None = None):
        # ── Short-term memory: PostgreSQL (mandatory) ─────
        from src.memory.pg_backend import get_pg_backend
        self._pg = get_pg_backend()

        if self._pg is None or not self._pg._available:
            raise RuntimeError(
                "PostgreSQL is required but not available. "
                "Check JARVIS_PG_DSN, JARVIS_PG_PASSWORD, and database configuration."
            )

        log.info("Using PostgreSQL for conversation history")
        # Placeholder so existing code that touches self.conn doesn't crash
        self._db_lock = threading.Lock()
        self.conn = None

        # ── Weaviate — vector semantic long-term memory ─────────────────
        self._weaviate = None
        try:
            from src.memory.weaviate_backend import get_weaviate
            wv = get_weaviate()
            if wv.available:
                self._weaviate = wv
                log.info("Weaviate semantic memory connected")
            else:
                log.warning("Weaviate not available — long-term memory disabled")
        except Exception as e:
            log.warning("Weaviate init failed: %s — long-term memory disabled", e)

    # ── Conversation Log ───────────────────────────────────────────────

    # Patterns that should never be stored in conversation memory
    _STRIP_PATTERNS = [
        ("[_voice_context_hints", "[/voice_context_hints]"),
        ("<system-reminder>", "</system-reminder>"),
    ]

    def add_turn(self, role: str, content: str):
        """Log a conversation turn. Does NOT absorb into lattice —
        only explicit learn() calls go into the lattice."""
        # Strip injected context blocks — store only the clean user/jarvis text
        clean = content
        for start_tag, end_tag in self._STRIP_PATTERNS:
            while start_tag in clean:
                s = clean.find(start_tag)
                e = clean.find(end_tag, s)
                if e == -1:
                    clean = clean[:s].rstrip()
                    break
                clean = clean[:s].rstrip() + clean[e + len(end_tag):]
        clean = clean.strip()
        if not clean:
            return

        if self._pg:
            self._pg.add_turn(role, clean)
            return
        # PostgreSQL is required
        raise RuntimeError("PostgreSQL backend unavailable for add_turn()")

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

        if self._pg:
            return self._pg.get_history(
                limit=limit, time_cutoff=time_cutoff, effective_limit=effective_limit
            )
        raise RuntimeError("PostgreSQL backend required but not available")

    def list_sessions(self, gap_minutes: int = 30) -> list[dict]:
        """Group conversation turns into sessions by time gap.

        A new session starts whenever two consecutive messages are more than
        `gap_minutes` apart.  Returns a list of sessions sorted newest-first,
        each with: id (start_ts), title (first user message), start_ts, end_ts,
        message_count.
        """
        if self._pg:
            return self._pg.list_sessions(gap_minutes)
        raise RuntimeError("PostgreSQL backend required but not available")

    def delete_session(self, start_ts: float, end_ts: float) -> int:
        """Delete all turns whose timestamp falls within [start_ts, end_ts].
        Returns the number of rows deleted.
        """
        if self._pg:
            return self._pg.delete_session(start_ts, end_ts)
        raise RuntimeError("PostgreSQL backend required but not available")

    # ── Long-term Learning ────────────────────────────────────────────

    def learn(
        self,
        content: str,
        node_type: NodeType = NodeType.FACT,
        tags: list[str] | None = None,
    ) -> MemoryNode:
        """Learn a new piece of knowledge — store in Weaviate.

        Guards applied:
          1. Prompt injection detection — adversarial inputs rejected.
          2. Near-duplicate dedup — 0.95+ similarity skipped.
        """
        # Guard 1: prompt injection detection
        try:
            from src.security.prompt_injection import is_prompt_injection
            if is_prompt_injection(content):
                log.warning("learn() blocked — prompt injection detected (%.60r)", content)
                # Return a stub node without storing
                node_id = f"blocked_{hash(content) & 0xFFFFFFFF:08x}"
                return MemoryNode(
                    id=node_id,
                    content="[blocked]",
                    node_type=NodeType.FACT,
                    strength=0.0,
                    tags=tags or []
                )
        except Exception:
            pass

        # Guard 2: near-duplicate dedup
        try:
            from src.memory.dedup import get_deduplicator
            dedup = get_deduplicator()
            if dedup.check_and_add(content):
                log.debug("learn() skipped — near-duplicate (>= 0.95 similarity)")
                node_id = f"dup_{hash(content) & 0xFFFFFFFF:08x}"
                return MemoryNode(
                    id=node_id,
                    content=content,
                    node_type=node_type,
                    strength=0.5,
                    tags=tags or []
                )
        except Exception:
            pass

        # Store in Weaviate
        node_id = f"wv_{hash(content) & 0xFFFFFFFF:08x}"
        strength = 1.0

        if self._weaviate:
            try:
                success = self._weaviate.store(
                    content=content,
                    node_type=node_type.value,
                    tags=tags or [],
                    strength=strength,
                )
                if not success:
                    log.debug("Weaviate store returned False for: %.60r", content)
            except Exception as e:
                log.debug("Weaviate store failed: %s", e)
        else:
            log.warning("Weaviate unavailable — learn() has no backend")

        return MemoryNode(
            id=node_id,
            content=content,
            node_type=node_type,
            strength=strength,
            tags=tags or [],
            timestamp=time.time()
        )

    def recall(self, query: str, top_k: int = 5) -> list[MemoryNode]:
        """Recall semantic memories from Weaviate.

        Returns a sorted list of MemoryNode by strength.
        """
        if not self._weaviate:
            log.warning("Weaviate unavailable — recall() returns empty list")
            return []

        try:
            wv_results = self._weaviate.recall(query, top_k=top_k)
            results = []
            for item in wv_results:
                content = item.get("content", "")
                if not content:
                    continue
                try:
                    nt = NodeType(item.get("node_type", "fact"))
                except (ValueError, KeyError):
                    nt = NodeType.FACT
                node = MemoryNode(
                    id=f"wv_{hash(content) & 0xFFFFFFFF:08x}",
                    content=content,
                    node_type=nt,
                    strength=item.get("score", 0.6) * item.get("strength", 1.0),
                    tags=item.get("tags", []),
                )
                results.append(node)
            # Already sorted by Weaviate, but ensure it
            results.sort(key=lambda n: n.strength, reverse=True)
            return results[:top_k]
        except Exception as e:
            log.error("Weaviate recall failed: %s", e)
            return []

    def recall_as_context(self, query: str, top_k: int = 5, max_chars: int = 3000) -> str:
        """Recall semantic knowledge as context for the LLM.

        Returns formatted facts, skills, and concepts.
        Also includes memdir and RAG context.
        """
        # Get semantic memories from Weaviate
        memories = self.recall(query, top_k=top_k * 2)

        # Filter: only knowledge, not episodic
        filtered = [
            m for m in memories
            if m.node_type in (NodeType.FACT, NodeType.SKILL, NodeType.CONCEPT, NodeType.ENTITY)
            and m.strength > 0.3
        ][:top_k]

        lines = []
        if filtered:
            lines.append("[Known facts:]")
            for mem in filtered:
                lines.append(f"  - {mem.content}")

        # Also search memdir for file-based memories
        try:
            memdir_results = memdir_search(query, max_results=min(3, top_k))
            if memdir_results:
                lines.append("[Memory files:]")
                for entry in memdir_results:
                    preview = entry.content[:150].replace("\n", " ")
                    lines.append(f"  - [{entry.id}] {preview}")
        except (OSError, AttributeError) as e:
            log.debug("Memdir search failed: %s", e)

        # RAG knowledge base — injected automatically.
        # Budget: up to 1/3 of max_chars so it doesn't crowd out semantic memories.
        rag_budget = max_chars // 3
        try:
            from src.rag import get_pipeline
            pipeline = get_pipeline()
            if pipeline.stats().get("chunks", 0) > 0:
                rag_ctx = pipeline.query_as_context(query, k=3, max_chars=rag_budget)
                if rag_ctx:
                    lines.append(rag_ctx)
        except Exception as e:
            log.debug("RAG context injection failed: %s", e)

        result = "\n".join(lines) if lines else ""
        if result and len(result) > max_chars:
            result = result[:max_chars] + "\n  ... (memory truncated)"
        return result

    # ── Stub methods for backwards compatibility ───────────────────────

    def recall_domain(self, domain: str, top_k: int = 10) -> list[MemoryNode]:
        """Recall facts tagged with a domain (forwards to semantic search)."""
        return self.recall(domain, top_k)

    def recall_entity(self, entity: str, top_k: int = 10) -> list[MemoryNode]:
        """Recall everything about an entity (forwards to semantic search)."""
        return self.recall(entity, top_k)

    def recall_recent(self, top_k: int = 10) -> list[MemoryNode]:
        """Recall recent memories (Weaviate provides recency via timestamp)."""
        return self.recall("recent", top_k)

    def recall_skills(self, top_k: int = 20) -> list[MemoryNode]:
        """Recall all learned skills."""
        return self.recall("skill", top_k)

    def knowledge_gaps(self, domain: str) -> dict:
        """Analyze knowledge gaps (placeholder)."""
        return {"message": "Knowledge gaps analysis not available in Weaviate-only mode"}

    def get_associations(self, query: str) -> list[tuple[MemoryNode, float]]:
        """Get associations (placeholder)."""
        results = self.recall(query, top_k=1)
        return [(r, 1.0) for r in results]

    # ── Maintenance ────────────────────────────────────────────────────

    def save(self):
        """No-op for Weaviate (persists automatically)."""
        pass

    def maintain(self):
        """Maintenance is handled by Weaviate automatically."""
        return {"status": "Weaviate handles maintenance automatically"}

    @property
    def stats(self) -> dict:
        """Get memory system stats."""
        conv_count = 0
        if self._pg:
            try:
                history = self._pg.get_history(limit=9999999)
                conv_count = len(history)
            except Exception:
                pass

        wv_count = 0
        if self._weaviate:
            try:
                import weaviate
                client = weaviate.connect_to_local()
                collection = client.collections.get("JARVISMemory")
                wv_count = collection.aggregate.over_all(group_by=None).total_count
                client.close()
            except Exception as e:
                log.debug("Weaviate stats failed: %s", e)

        return {
            "conversations": conv_count,
            "long_term_memories": wv_count,
        }

    def close(self):
        """Shut down the memory system."""
        if self._weaviate:
            try:
                self._weaviate.close()
            except Exception:
                pass
        if self._pg:
            self._pg.close()
