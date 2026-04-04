"""JARVIS Memory Index — speed-of-light recall.

The lattice stores memories. This module FINDS them.

Problem: Scanning every node with string matching is O(n). As JARVIS learns
more, recall gets slower. That's the opposite of how a brain works — a human
with more knowledge doesn't take longer to remember, they take LESS time
because they have more connections to activate.

Solution: Inverted indexes. Like a search engine for your own mind.

Every word, tag, domain, type, and relationship is mapped to the set of
node IDs that contain it. Recall becomes O(1) lookup + intersection,
not O(n) scan.

Index types:
- WORD INDEX: word → {node_ids}  — "python" → all nodes mentioning python
- TAG INDEX: tag → {node_ids}    — "security" → all security-tagged nodes
- TYPE INDEX: NodeType → {node_ids} — SKILL → all skill nodes
- DOMAIN INDEX: domain → {node_ids} — "coding" → all coding knowledge
- ENTITY INDEX: entity → {node_ids} — "Ulrich" → all nodes about Ulrich
- TEMPORAL INDEX: sorted by recency  — "what was I just thinking about?"
- KEYWORD INDEX: extracted concepts → {node_ids} — semantic keywords
"""

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from src.memory.lattice.node import MemoryNode, NodeType


# Words that carry no meaning for recall — skip them in indexing
STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "and", "or", "but", "not", "no", "nor", "so", "yet", "both", "either",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up",
    "about", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "any", "few", "more", "most", "some",
    "very", "just", "also", "too", "only", "own", "same", "than",
    "if", "as", "because", "while", "until", "although", "even",
})

# Domain classification keywords — maps words to knowledge domains
DOMAIN_KEYWORDS = {
    "security": {"hack", "exploit", "vulnerability", "nmap", "scan", "port", "firewall",
                 "password", "crack", "brute", "injection", "xss", "sql", "payload",
                 "metasploit", "burp", "wireshark", "pentest", "ctf", "reverse",
                 "malware", "phishing", "encryption", "decrypt", "cipher", "hash",
                 "authentication", "authorization", "privilege", "escalation", "shell",
                 "backdoor", "trojan", "worm", "rootkit", "forensic", "opsec"},
    "coding": {"python", "rust", "javascript", "code", "function", "class", "variable",
               "loop", "array", "list", "dict", "string", "int", "float", "bool",
               "import", "module", "package", "api", "http", "json", "database",
               "sql", "git", "commit", "branch", "merge", "compile", "debug",
               "error", "exception", "test", "algorithm", "data", "structure"},
    "system": {"linux", "kali", "terminal", "command", "bash", "shell", "process",
               "file", "directory", "path", "permission", "user", "root", "sudo",
               "service", "daemon", "systemctl", "network", "ip", "dns", "ssh",
               "package", "install", "update", "kernel", "driver", "mount", "disk"},
    "personal": {"ulrich", "prefer", "like", "hate", "favorite", "birthday", "work",
                 "home", "friend", "family", "hobby", "want", "need", "feel",
                 "habit", "routine", "always", "never", "morning", "night"},
    "project": {"jarvis", "brain", "plugin", "evolve", "deploy", "build", "feature",
                "bug", "fix", "improve", "upgrade", "module", "shell", "web",
                "mobile", "desktop", "architecture", "design", "plan"},
}


@dataclass
class RecallResult:
    """A memory recall result with relevance scoring."""
    node: MemoryNode
    score: float
    match_sources: list[str] = field(default_factory=list)  # why this matched


class MemoryIndex:
    """Lightning-fast memory indexing and recall.

    Maintains multiple inverted indexes that update in real-time
    as memories are added, accessed, or removed.

    Recall is O(1) lookup + scoring, not O(n) scan.
    """

    def __init__(self):
        # Inverted indexes: value → set of node IDs
        self._word_index: dict[str, set[str]] = defaultdict(set)
        self._tag_index: dict[str, set[str]] = defaultdict(set)
        self._type_index: dict[NodeType, set[str]] = defaultdict(set)
        self._domain_index: dict[str, set[str]] = defaultdict(set)
        self._entity_index: dict[str, set[str]] = defaultdict(set)
        self._keyword_index: dict[str, set[str]] = defaultdict(set)

        # Temporal index: node IDs sorted by last access time
        self._temporal: list[tuple[float, str]] = []  # (timestamp, node_id)

        # Reverse lookup: node_id → set of indexed words (for removal)
        self._node_words: dict[str, set[str]] = defaultdict(set)
        self._node_domains: dict[str, set[str]] = defaultdict(set)
        self._node_entities: dict[str, set[str]] = defaultdict(set)
        self._node_keywords: dict[str, set[str]] = defaultdict(set)

        # Stats
        self.total_indexed = 0

    def index_node(self, node: MemoryNode):
        """Index a memory node across all indexes. O(w) where w = word count."""
        nid = node.id
        content_lower = node.content.lower()

        # 1. Word index — every meaningful word
        words = self._extract_words(content_lower)
        for word in words:
            self._word_index[word].add(nid)
        self._node_words[nid] = words

        # 2. Tag index
        for tag in node.tags:
            self._tag_index[tag.lower()].add(nid)

        # 3. Type index
        self._type_index[node.node_type].add(nid)

        # 4. Domain classification
        domains = self._classify_domains(words)
        for domain in domains:
            self._domain_index[domain].add(nid)
        self._node_domains[nid] = domains

        # 5. Entity extraction
        entities = self._extract_entities(node.content)
        for entity in entities:
            self._entity_index[entity].add(nid)
        self._node_entities[nid] = entities

        # 6. Keyword extraction — conceptual terms, not just words
        keywords = self._extract_keywords(content_lower, words)
        for kw in keywords:
            self._keyword_index[kw].add(nid)
        self._node_keywords[nid] = keywords

        # 7. Temporal index
        self._temporal.append((node.last_accessed, nid))

        self.total_indexed += 1

    def remove_node(self, node_id: str):
        """Remove a node from all indexes. O(w)."""
        # Word index
        for word in self._node_words.get(node_id, set()):
            self._word_index[word].discard(node_id)
            if not self._word_index[word]:
                del self._word_index[word]
        self._node_words.pop(node_id, None)

        # Tag index
        for tag_nodes in self._tag_index.values():
            tag_nodes.discard(node_id)

        # Type index
        for type_nodes in self._type_index.values():
            type_nodes.discard(node_id)

        # Domain index
        for domain in self._node_domains.get(node_id, set()):
            self._domain_index[domain].discard(node_id)
        self._node_domains.pop(node_id, None)

        # Entity index
        for entity in self._node_entities.get(node_id, set()):
            self._entity_index[entity].discard(node_id)
        self._node_entities.pop(node_id, None)

        # Keyword index
        for kw in self._node_keywords.get(node_id, set()):
            self._keyword_index[kw].discard(node_id)
        self._node_keywords.pop(node_id, None)

        # Temporal index — lazy removal (filtered at query time)
        self.total_indexed -= 1

    def recall(self, query: str, nodes: dict[str, MemoryNode], top_k: int = 5) -> list[RecallResult]:
        """Recall memories matching a query. O(1) lookup + scoring.

        This is the fast path. Instead of scanning every node:
        1. Extract query words → look up each in word index → union of node IDs
        2. Score each candidate by: word overlap, domain match, tag match, recency, strength
        3. Return top-k
        """
        query_lower = query.lower()
        query_words = self._extract_words(query_lower)
        query_keywords = self._extract_keywords(query_lower, query_words)
        query_entities = self._extract_entities(query)
        query_domains = self._classify_domains(query_words)

        # Phase 1: Gather candidates from indexes (O(1) per index)
        candidates: dict[str, float] = {}
        match_sources: dict[str, list[str]] = defaultdict(list)

        # Word matches — highest signal
        for word in query_words:
            for nid in self._word_index.get(word, set()):
                candidates[nid] = candidates.get(nid, 0) + 0.15
                if "word" not in match_sources[nid]:
                    match_sources[nid].append("word")

        # Keyword matches — semantic signal
        for kw in query_keywords:
            for nid in self._keyword_index.get(kw, set()):
                candidates[nid] = candidates.get(nid, 0) + 0.25
                if "keyword" not in match_sources[nid]:
                    match_sources[nid].append("keyword")

        # Entity matches — strong signal
        for entity in query_entities:
            for nid in self._entity_index.get(entity, set()):
                candidates[nid] = candidates.get(nid, 0) + 0.35
                if "entity" not in match_sources[nid]:
                    match_sources[nid].append("entity")

        # Domain matches — contextual signal
        for domain in query_domains:
            for nid in self._domain_index.get(domain, set()):
                candidates[nid] = candidates.get(nid, 0) + 0.1
                if "domain" not in match_sources[nid]:
                    match_sources[nid].append("domain")

        # Tag matches
        for word in query_words:
            for nid in self._tag_index.get(word, set()):
                candidates[nid] = candidates.get(nid, 0) + 0.2
                if "tag" not in match_sources[nid]:
                    match_sources[nid].append("tag")

        if not candidates:
            return []

        # Phase 2: Score candidates
        results = []
        for nid, index_score in candidates.items():
            node = nodes.get(nid)
            if not node or not node.is_alive:
                continue

            score = index_score

            # Exact substring match bonus
            if query_lower in node.content.lower():
                score += 0.5

            # Word overlap ratio — how much of the query is covered
            node_words = self._node_words.get(nid, set())
            if query_words and node_words:
                overlap = len(query_words & node_words)
                score += 0.3 * (overlap / len(query_words))

            # Strength weighting — strong memories surface first
            score *= (0.5 + node.strength * 0.5)

            # Recency boost — recently accessed memories get a small bump
            hours_ago = (time.time() - node.last_accessed) / 3600
            if hours_ago < 1:
                score *= 1.3
            elif hours_ago < 24:
                score *= 1.1

            # Type bonus — skills and entities are often more useful
            if node.node_type in (NodeType.SKILL, NodeType.ENTITY):
                score *= 1.15

            results.append(RecallResult(
                node=node,
                score=score,
                match_sources=match_sources.get(nid, []),
            ))

        # Sort by score, return top-k
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def recall_by_domain(self, domain: str, nodes: dict[str, MemoryNode],
                         top_k: int = 10) -> list[MemoryNode]:
        """Instantly recall all memories in a domain. O(1)."""
        nids = self._domain_index.get(domain.lower(), set())
        results = []
        for nid in nids:
            node = nodes.get(nid)
            if node and node.is_alive:
                results.append(node)
        results.sort(key=lambda n: n.strength * (n.access_count + 1), reverse=True)
        return results[:top_k]

    def recall_by_entity(self, entity: str, nodes: dict[str, MemoryNode],
                         top_k: int = 10) -> list[MemoryNode]:
        """Recall all memories about a specific entity. O(1)."""
        nids = self._entity_index.get(entity.lower(), set())
        results = [nodes[nid] for nid in nids if nid in nodes and nodes[nid].is_alive]
        results.sort(key=lambda n: n.strength, reverse=True)
        return results[:top_k]

    def recall_recent(self, nodes: dict[str, MemoryNode], top_k: int = 10) -> list[MemoryNode]:
        """Recall most recently accessed memories. O(k)."""
        # Sort temporal index by recency
        self._temporal.sort(key=lambda x: x[0], reverse=True)
        results = []
        seen = set()
        for _, nid in self._temporal:
            if nid in seen:
                continue
            seen.add(nid)
            node = nodes.get(nid)
            if node and node.is_alive:
                results.append(node)
                if len(results) >= top_k:
                    break
        return results

    def recall_by_type(self, node_type: NodeType, nodes: dict[str, MemoryNode],
                       top_k: int = 20) -> list[MemoryNode]:
        """Recall all memories of a specific type. O(1)."""
        nids = self._type_index.get(node_type, set())
        results = [nodes[nid] for nid in nids if nid in nodes and nodes[nid].is_alive]
        results.sort(key=lambda n: n.strength, reverse=True)
        return results[:top_k]

    def get_domains_for_query(self, query: str) -> set[str]:
        """Classify which knowledge domains a query touches."""
        words = self._extract_words(query.lower())
        return self._classify_domains(words)

    def find_knowledge_gaps(self, domain: str, nodes: dict[str, MemoryNode]) -> dict:
        """Analyze what JARVIS knows vs doesn't know in a domain.

        Returns stats about knowledge coverage — used by curiosity engine.
        """
        domain_nodes = self._domain_index.get(domain.lower(), set())
        alive = [nid for nid in domain_nodes if nid in nodes and nodes[nid].is_alive]
        strong = [nid for nid in alive if nodes[nid].is_strong]
        skills = [nid for nid in alive if nodes[nid].node_type == NodeType.SKILL]
        facts = [nid for nid in alive if nodes[nid].node_type == NodeType.FACT]

        return {
            "domain": domain,
            "total_memories": len(alive),
            "strong_memories": len(strong),
            "skills": len(skills),
            "facts": len(facts),
            "coverage": "deep" if len(strong) > 10 else "moderate" if len(alive) > 5 else "shallow",
        }

    def rebuild(self, nodes: dict[str, MemoryNode]):
        """Rebuild all indexes from scratch. Used after loading from disk."""
        self._word_index.clear()
        self._tag_index.clear()
        self._type_index.clear()
        self._domain_index.clear()
        self._entity_index.clear()
        self._keyword_index.clear()
        self._temporal.clear()
        self._node_words.clear()
        self._node_domains.clear()
        self._node_entities.clear()
        self._node_keywords.clear()
        self.total_indexed = 0

        for node in nodes.values():
            if node.is_alive:
                self.index_node(node)

    # ── Internal extraction methods ─────────────────────────────────

    def _extract_words(self, text: str) -> set[str]:
        """Extract meaningful words from text (skip stop words)."""
        raw = re.findall(r'[a-z0-9]+', text)
        return {w for w in raw if w not in STOP_WORDS and len(w) > 1}

    def _classify_domains(self, words: set[str]) -> set[str]:
        """Classify which domains a set of words belongs to."""
        domains = set()
        for domain, keywords in DOMAIN_KEYWORDS.items():
            if words & keywords:
                domains.add(domain)
        return domains

    def _extract_entities(self, text: str) -> set[str]:
        """Extract named entities (capitalized words, proper nouns)."""
        # Simple but fast: words starting with uppercase that aren't sentence starters
        entities = set()
        words = text.split()
        for i, word in enumerate(words):
            clean = re.sub(r'[^a-zA-Z]', '', word)
            if not clean or len(clean) < 2:
                continue
            # Capitalized and not first word (or after period)
            if clean[0].isupper() and (i > 0 or len(clean) > 3):
                # Skip common non-entity capitals
                if clean.lower() not in STOP_WORDS and clean not in ("JARVIS", "CONCEPT"):
                    entities.add(clean.lower())
        # Always index "ulrich" and "jarvis" if present
        text_lower = text.lower()
        if "ulrich" in text_lower:
            entities.add("ulrich")
        if "jarvis" in text_lower:
            entities.add("jarvis")
        return entities

    def _extract_keywords(self, text_lower: str, words: set[str]) -> set[str]:
        """Extract conceptual keywords — multi-word phrases and semantic terms.

        These bridge the gap between literal words and meaning.
        "programming language" is a keyword. "programming" and "language" alone
        might match cooking recipes or foreign languages.
        """
        keywords = set()

        # Bigrams — two-word phrases that carry meaning
        word_list = re.findall(r'[a-z0-9]+', text_lower)
        for i in range(len(word_list) - 1):
            w1, w2 = word_list[i], word_list[i + 1]
            if w1 not in STOP_WORDS and w2 not in STOP_WORDS:
                if len(w1) > 2 and len(w2) > 2:
                    keywords.add(f"{w1}_{w2}")

        # Technical terms — compound words that are concepts
        tech_patterns = [
            r'(?:machine|deep)\s+learning',
            r'(?:web|mobile|desktop)\s+app(?:lication)?',
            r'(?:data|code|text)\s+(?:base|structure|file)',
            r'(?:command|terminal|shell)\s+(?:line|prompt)',
            r'(?:ip|mac|dns)\s+address',
            r'(?:api|ssh|ssl|tls|http)\s+(?:key|token|server|client)',
            r'(?:file|folder|path)\s+(?:system|name|permission)',
        ]
        for pattern in tech_patterns:
            match = re.search(pattern, text_lower)
            if match:
                keywords.add(match.group(0).replace(" ", "_"))

        return keywords

    @property
    def stats(self) -> dict:
        return {
            "indexed_nodes": self.total_indexed,
            "unique_words": len(self._word_index),
            "unique_tags": len(self._tag_index),
            "unique_entities": len(self._entity_index),
            "unique_keywords": len(self._keyword_index),
            "domains": {d: len(nids) for d, nids in self._domain_index.items()},
        }
