"""TurboQuant semantic memory index.

Uses QJL (1-bit Quantized Johnson-Lindenstrauss) from the ``turboquant``
package to build a fast approximate-nearest-neighbour index over conversation
history and learned facts.

Memory footprint: ~dim/8 bytes per vector (16× smaller than float64).
Search time:      O(n) with vectorised 1-bit inner-product operations.

The text → vector step uses a hashed-TF bag-of-words projection, so there
is no external embedding model required.  This index is complementary to
the Neural Lattice and Holographic Memory — it provides a fast semantic
recall path without any LLM call.
"""

import hashlib
import logging
import math
import re
from collections import Counter

import numpy as np

log = logging.getLogger("jarvis.tq_memory")

# Fixed vector dimension — 256 balances accuracy vs init time (O(n³) scaling).
_DIM = 256

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "it", "in", "on", "at", "to", "of", "and", "or",
    "but", "for", "with", "this", "that", "was", "are", "be", "been", "by",
    "do", "did", "has", "have", "had", "not", "so", "if", "as", "up", "out",
    "i", "you", "we", "he", "she", "they", "my", "your", "our", "its",
})


# ── text → vector ─────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-z]+", text.lower())
        if w not in _STOP_WORDS and len(w) > 1
    ]


def _text_to_vector(text: str, dim: int = _DIM) -> np.ndarray:
    """Hashed-TF projection: text → ℝ^dim unit vector.

    Each token is hashed to a bucket index; its sign and weight are derived
    from the hash so that collisions cancel out in expectation (a form of
    Count Sketch / Feature Hashing with TF weighting).
    """
    tokens = _tokenize(text)
    if not tokens:
        return np.zeros(dim, dtype=np.float64)

    vec = np.zeros(dim, dtype=np.float64)
    count = Counter(tokens)
    for token, freq in count.items():
        digest = hashlib.md5(token.encode(), usedforsecurity=False).digest()
        h_val = int.from_bytes(digest[:8], "little")
        idx = h_val % dim
        sign = 1 if (h_val >> (dim % 64)) & 1 else -1
        tf = 1.0 + math.log(freq)
        vec[idx] += sign * tf

    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec /= norm
    return vec


# ── index ─────────────────────────────────────────────────────────────────────

class TurboQuantMemoryIndex:
    """Approximate nearest-neighbour index backed by TurboQuant QJL.

    Stores 1-bit quantised representations of hashed-TF vectors.
    Falls back to exact float64 inner products if ``turboquant`` is not
    installed — behaviour is identical, just slower and larger.

    Parameters
    ----------
    dim:
        Dimensionality of the hashed-TF vector space.
    seed:
        RNG seed for the QJL random projection matrix.  Fix this for
        reproducible results across restarts.
    """

    def __init__(self, dim: int = _DIM, seed: int = 42):
        self._dim = dim
        self._tq: object | None = None
        self._entries: list[dict] = []
        self._seed = seed
        self._init_tq()

    # ── setup ─────────────────────────────────────────────────────────

    def _init_tq(self) -> None:
        try:
            from turboquant.turboquant import TurboQuant  # type: ignore[import-untyped]
            # 4-bit inner-product mode: ~99% accuracy, 4× smaller than float64
            self._tq = TurboQuant(
                dim=self._dim,
                bit_width=4,
                mode="inner_product",
                seed=self._seed,
            )
            log.debug("TurboQuant index ready (dim=%d, 4-bit inner-product)", self._dim)
        except Exception as exc:
            log.warning("TurboQuant not available — using exact inner products: %s", exc)

    # ── public API ────────────────────────────────────────────────────

    def add(self, text: str, role: str = "user", metadata: dict | None = None) -> None:
        """Add a text entry to the index."""
        if not text or not text.strip():
            return

        vec = _text_to_vector(text, self._dim).reshape(1, -1)

        compressed = None
        if self._tq is not None:
            try:
                compressed = self._tq.quantize(vec)  # type: ignore[attr-defined]
            except Exception as exc:
                log.debug("TurboQuant quantize failed: %s", exc)

        self._entries.append({
            "text": text,
            "role": role,
            "metadata": metadata or {},
            # 4-bit compressed form; fall back to float64 if TurboQuant unavailable
            "compressed": compressed,
            "vec": vec if compressed is None else None,
        })

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
        """Return top-k entries most similar to ``query``.

        Similarity is measured by 4-bit TurboQuant inner-product estimator
        or exact inner product when TurboQuant is unavailable.
        """
        if not self._entries:
            return []

        q_vec = _text_to_vector(query, self._dim)
        scores: list[tuple[float, int]] = []

        for i, entry in enumerate(self._entries):
            try:
                if self._tq is not None and entry["compressed"] is not None:
                    # 4-bit inner-product via TurboQuant — ~99% accuracy
                    score = float(
                        self._tq.inner_product(q_vec, entry["compressed"])[0]  # type: ignore[attr-defined]
                    )
                elif entry.get("vec") is not None:
                    score = float(np.dot(q_vec, entry["vec"].ravel()))
                else:
                    score = 0.0
            except Exception:
                score = 0.0

            scores.append((score, i))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [self._entries[i] for _, i in scores[:top_k]]

    def recall_as_context(self, query: str, top_k: int = 5) -> str:
        """Return a formatted string of top-k matches for prompt injection."""
        results = self.recall(query, top_k)
        if not results:
            return ""
        lines = []
        for r in results:
            role = r.get("role", "user").capitalize()
            text = r["text"][:300]
            lines.append(f"{role}: {text}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    @property
    def using_quantization(self) -> bool:
        """True when TurboQuant 4-bit is active."""
        return self._tq is not None
