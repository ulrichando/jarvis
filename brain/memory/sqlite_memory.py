"""JARVIS SQLite Memory — persistent knowledge base with vector search.

JARVIS learns from every conversation and research session.
Knowledge is stored with embeddings (nomic-embed-text) for semantic recall.

Tables:
- conversations: full chat history
- knowledge: learned facts with embeddings — JARVIS's growing brain
- user_profile: user preferences and attributes
"""

import sqlite3
import time
import struct
import math
from pathlib import Path
from brain.config import DATA_DIR

EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Fast cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _pack_embedding(vec: list[float]) -> bytes:
    """Pack float list to compact bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack bytes to float list."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


class SQLiteMemory:
    """Persistent memory with vector-powered semantic search.

    JARVIS's brain grows over time:
    - Learns from every conversation
    - Learns from web research
    - Stores knowledge with embeddings for semantic recall
    - Keyword search as fallback when embeddings unavailable
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(DATA_DIR / "jarvis_memory.db")

        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        self._migrate()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT DEFAULT '',
                model TEXT DEFAULT '',
                latency_ms INTEGER DEFAULT 0,
                timestamp REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(timestamp);

            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL UNIQUE,
                source TEXT DEFAULT 'conversation',
                category TEXT DEFAULT 'general',
                importance REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                embedding BLOB DEFAULT NULL,
                created_at REAL NOT NULL,
                last_accessed REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_know_cat ON knowledge(category);
            CREATE INDEX IF NOT EXISTS idx_know_imp ON knowledge(importance DESC);

            CREATE TABLE IF NOT EXISTS user_profile (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        self.conn.commit()

    def _migrate(self):
        """Handle schema changes from older databases."""
        # Rename 'facts' table to 'knowledge' if old schema exists
        tables = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "facts" in tables and "knowledge" not in tables:
            self.conn.execute("ALTER TABLE facts RENAME TO knowledge")
            self.conn.commit()
        # Add embedding column if missing
        if "knowledge" in tables:
            try:
                self.conn.execute("SELECT embedding FROM knowledge LIMIT 1")
            except sqlite3.OperationalError:
                self.conn.execute("ALTER TABLE knowledge ADD COLUMN embedding BLOB DEFAULT NULL")
                self.conn.commit()
        # Add category column if missing (old 'tag' → 'category')
        if "knowledge" in tables:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(knowledge)").fetchall()]
            if "category" not in cols and "tag" in cols:
                self.conn.execute("ALTER TABLE knowledge RENAME COLUMN tag TO category")
                self.conn.commit()

    # ── Embedding ──────────────────────────────────────────────────

    def _get_embedding(self, text: str) -> list[float] | None:
        """Get embedding from Ollama nomic-embed-text."""
        try:
            import requests
            r = requests.post(
                EMBED_URL,
                json={"model": EMBED_MODEL, "input": text},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                embeddings = data.get("embeddings", [])
                return embeddings[0] if embeddings else None
        except Exception:
            pass
        return None

    # ── Conversations ──────────────────────────────────────────────

    def log_conversation(self, role: str, content: str, intent: str = "",
                         model: str = "", latency_ms: int = 0):
        self.conn.execute(
            "INSERT INTO conversations (role, content, intent, model, latency_ms, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (role, content, intent, model, latency_ms, time.time()),
        )
        self.conn.commit()

    def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content, intent, model, timestamp FROM conversations "
            "ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def conversation_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()
        return row[0] if row else 0

    # ── Knowledge (JARVIS's growing brain) ─────────────────────────

    def learn(self, content: str, source: str = "conversation",
              category: str = "general", importance: float = 0.5):
        """Store knowledge with embedding. JARVIS grows smarter."""
        if not content or len(content) < 5:
            return

        # Skip duplicates — boost importance on re-learn
        existing = self.conn.execute(
            "SELECT id FROM knowledge WHERE content = ?", (content,)
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE knowledge SET importance = MIN(1.0, importance + 0.1), "
                "access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                (time.time(), existing[0]),
            )
            self.conn.commit()
            return

        # Generate embedding for semantic search
        embedding = self._get_embedding(content)
        emb_bytes = _pack_embedding(embedding) if embedding else None

        now = time.time()
        self.conn.execute(
            "INSERT OR IGNORE INTO knowledge "
            "(content, source, category, importance, embedding, created_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (content, source, category, importance, emb_bytes, now, now),
        )
        self.conn.commit()

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search — find relevant knowledge using embeddings.

        Falls back to keyword search if embeddings unavailable.
        """
        # Try semantic search first
        query_emb = self._get_embedding(query)
        if query_emb:
            return self._semantic_search(query_emb, limit)

        # Fallback: keyword search
        return self._keyword_search(query, limit)

    def _semantic_search(self, query_emb: list[float], limit: int) -> list[dict]:
        """Search knowledge by cosine similarity with embeddings."""
        rows = self.conn.execute(
            "SELECT id, content, category, importance, source, embedding "
            "FROM knowledge WHERE embedding IS NOT NULL"
        ).fetchall()

        if not rows:
            return []

        # Score each fact by cosine similarity
        scored = []
        for row in rows:
            emb = _unpack_embedding(row["embedding"])
            sim = _cosine_similarity(query_emb, emb)
            # Blend similarity with importance
            score = sim * 0.8 + row["importance"] * 0.2
            if sim > 0.3:  # Relevance threshold
                scored.append((score, dict(row)))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Update access counts for recalled knowledge
        results = []
        for score, row in scored[:limit]:
            row.pop("embedding", None)
            row["relevance"] = round(score, 3)
            results.append(row)
            self.conn.execute(
                "UPDATE knowledge SET access_count = access_count + 1, "
                "last_accessed = ? WHERE id = ?",
                (time.time(), row["id"]),
            )

        if results:
            self.conn.commit()
        return results

    def _keyword_search(self, query: str, limit: int) -> list[dict]:
        """Fallback keyword search when embeddings unavailable."""
        words = [w for w in query.lower().split() if len(w) >= 3]
        if not words:
            return []

        conditions = []
        params = []
        for word in words[:5]:
            conditions.append("LOWER(content) LIKE ?")
            params.append(f"%{word}%")

        where = " OR ".join(conditions)
        params.append(limit)

        rows = self.conn.execute(
            f"SELECT content, category, importance, source FROM knowledge "
            f"WHERE {where} ORDER BY importance DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def knowledge_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()
        return row[0] if row else 0

    def knowledge_with_embeddings(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE embedding IS NOT NULL"
        ).fetchone()
        return row[0] if row else 0

    # ── Bulk learning (from research) ──────────────────────────────

    def learn_from_research(self, research_text: str, source_url: str = ""):
        """Extract and store knowledge from research results."""
        # Split into sentences/facts
        sentences = [s.strip() for s in research_text.replace("\n", ". ").split(".")
                     if len(s.strip()) > 20 and len(s.strip()) < 500]

        source = source_url or "web_research"
        for sentence in sentences[:20]:  # Cap at 20 facts per research
            self.learn(sentence, source=source, category="research", importance=0.6)

    def learn_from_conversation(self, user_msg: str, jarvis_msg: str):
        """Extract knowledge from a conversation exchange."""
        # Store the exchange as a fact if it's substantial
        if len(jarvis_msg) > 30:
            fact = f"{user_msg.strip()} → {jarvis_msg.strip()[:200]}"
            self.learn(fact, source="conversation", category="dialogue", importance=0.3)

    # ── User Profile ───────────────────────────────────────────────

    def set_user(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO user_profile (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
        self.conn.commit()

    def get_user(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM user_profile WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def get_all_user(self) -> dict:
        rows = self.conn.execute("SELECT key, value FROM user_profile").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ── Stats ──────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "conversations": self.conversation_count(),
            "facts_stored": self.knowledge_count(),
            "facts_with_embeddings": self.knowledge_with_embeddings(),
            "user_attributes": len(self.get_all_user()),
            "db_path": self.db_path,
            "db_size_kb": round(Path(self.db_path).stat().st_size / 1024, 1)
            if Path(self.db_path).exists() else 0,
        }

    # ── Compat aliases ─────────────────────────────────────────────

    def store_fact(self, content: str, source: str = "conversation",
                   tag: str = "auto", importance: float = 0.5, **kw):
        """Backward compat — routes to learn()."""
        self.learn(content, source=source, category=tag, importance=importance)

    def recall_facts(self, query: str, limit: int = 5) -> list[dict]:
        """Backward compat — routes to recall()."""
        return self.recall(query, limit)

    def fact_count(self) -> int:
        """Backward compat."""
        return self.knowledge_count()

    def close(self):
        self.conn.close()
