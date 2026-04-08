"""Memory deduplication — skip storing near-duplicate content.

Uses TF-IDF cosine similarity to compare a new memory chunk against
recently stored chunks.  No external embedding model required.

If the `numpy` package is available the computation is vectorised;
otherwise it falls back to pure-Python sets (lower precision but zero deps).

Usage:
    from src.memory.dedup import MemoryDeduplicator

    dedup = MemoryDeduplicator(threshold=0.95)
    if dedup.is_duplicate(new_text):
        return  # skip — too similar to something already stored
    dedup.add(new_text)
    memory.store(new_text)
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, deque

log = logging.getLogger("jarvis.memory.dedup")

# ── Tokenisation ──────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"\b[a-z]{2,}\b")


def _tokenise(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


# ── TF-IDF cosine similarity (pure Python) ───────────────────────────────────

def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(tokens)
    total = max(len(tokens), 1)
    return {tok: (count / total) * idf.get(tok, 1.0) for tok, count in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Deduplicator ──────────────────────────────────────────────────────────────

class MemoryDeduplicator:
    """Sliding-window TF-IDF deduplicator for memory chunks.

    Args:
        threshold:   Similarity score (0–1) above which a chunk is considered
                     a duplicate and should be skipped.  0.95 is a safe default.
        window_size: How many recent chunks to compare against.  Larger windows
                     catch more duplicates but cost more CPU per check.
    """

    def __init__(self, threshold: float = 0.95, window_size: int = 200) -> None:
        self.threshold = threshold
        self._window: deque[list[str]] = deque(maxlen=window_size)
        self._df: Counter = Counter()       # document frequency per token
        self._n: int = 0                    # total documents seen

    # ── Public API ────────────────────────────────────────────────────

    def is_duplicate(self, text: str) -> bool:
        """Return True if *text* is too similar to a recently stored chunk."""
        if not text or not text.strip():
            return False

        tokens = _tokenise(text)
        if len(tokens) < 5:
            # Very short inputs — use exact match only
            return any(tokens == stored for stored in self._window)

        idf = self._idf()
        vec = _tfidf_vector(tokens, idf)

        for stored_tokens in self._window:
            stored_vec = _tfidf_vector(stored_tokens, idf)
            sim = _cosine(vec, stored_vec)
            if sim >= self.threshold:
                log.debug("Dedup hit (sim=%.3f): skipping near-duplicate chunk", sim)
                return True
        return False

    def add(self, text: str) -> None:
        """Record *text* as stored so future calls can compare against it."""
        tokens = _tokenise(text)
        if not tokens:
            return
        self._window.append(tokens)
        for tok in set(tokens):
            self._df[tok] += 1
        self._n += 1

    def check_and_add(self, text: str) -> bool:
        """Convenience: check + add in one call.

        Returns True if the text is a duplicate (should NOT be stored).
        Returns False if it's new (goes ahead and records it).
        """
        if self.is_duplicate(text):
            return True
        self.add(text)
        return False

    def reset(self) -> None:
        """Clear all stored history."""
        self._window.clear()
        self._df.clear()
        self._n = 0

    # ── Internal ──────────────────────────────────────────────────────

    def _idf(self) -> dict[str, float]:
        n = max(self._n, 1)
        return {
            tok: math.log((n + 1) / (df + 1)) + 1.0
            for tok, df in self._df.items()
        }


# ── Module-level singleton (shared across MemoryStore instances) ──────────────

_dedup: MemoryDeduplicator | None = None


def get_deduplicator(threshold: float = 0.95, window_size: int = 200) -> MemoryDeduplicator:
    """Return the global MemoryDeduplicator singleton."""
    global _dedup
    if _dedup is None:
        _dedup = MemoryDeduplicator(threshold=threshold, window_size=window_size)
    return _dedup
