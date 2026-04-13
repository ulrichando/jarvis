"""RAG Pipeline — unified ingest + query API.

Usage:
    from src.rag import get_pipeline
    pipeline = get_pipeline()

    # Ingest
    n = pipeline.ingest_file("/path/to/doc.pdf")
    n = pipeline.ingest_url("https://example.com/docs")
    n = pipeline.ingest_text("Raw text...", source="notes")

    # Query
    results = pipeline.query("What does X do?", k=5)
    context = pipeline.query_as_context("What does X do?")  # formatted string
"""

import logging
import os
from typing import List

from .loaders import load_file, load_url, load_string, load_directory
from .splitter import get_splitter
from .store import VectorStore, get_store, SearchResult

log = logging.getLogger(__name__)


class RAGPipeline:
    """Orchestrates load → split → embed → store → retrieve."""

    def __init__(self, store: VectorStore | None = None):
        self._store = store or get_store()

    # ── Ingest ────────────────────────────────────────────────────────

    def ingest_file(self, path: str) -> int:
        """Load, chunk, and embed a local file. Returns chunk count."""
        ext = os.path.splitext(path)[-1].lower()
        profile = "pdf" if ext == ".pdf" else "code" if ext in (
            ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
            ".java", ".c", ".cpp", ".cs", ".rb", ".sh",
        ) else "default"

        docs = load_file(path)
        return self._ingest_docs(docs, profile=profile, file_ext=ext)

    def ingest_url(self, url: str) -> int:
        """Load a web page and ingest it."""
        docs = load_url(url)
        return self._ingest_docs(docs, profile="web")

    def ingest_text(self, text: str, source: str = "inline") -> int:
        """Ingest a raw text string."""
        docs = load_string(text, source=source)
        return self._ingest_docs(docs, profile="default")

    def ingest_directory(self, path: str) -> int:
        """Recursively ingest all supported files in a directory."""
        docs = load_directory(path)
        return self._ingest_docs(docs, profile="default")

    def _ingest_docs(
        self,
        docs: List[tuple],
        profile: str = "default",
        file_ext: str | None = None,
    ) -> int:
        if not docs:
            log.warning("No documents loaded.")
            return 0

        splitter = get_splitter(profile=profile, file_ext=file_ext)
        all_chunks: List[str] = []
        all_metas: List[dict] = []

        for text, meta in docs:
            if not text or not text.strip():
                continue
            chunks = splitter.split_text(text)
            all_chunks.extend(chunks)
            all_metas.extend([meta.copy() for _ in chunks])

        if not all_chunks:
            log.warning("Splitting produced no chunks.")
            return 0

        n = self._store.add(all_chunks, all_metas)
        log.info("Ingested %d chunks from %d documents.", n, len(docs))
        return n

    # ── Query ─────────────────────────────────────────────────────────

    def query(self, question: str, k: int = 5, where: dict | None = None) -> List[SearchResult]:
        """Semantic search. Returns list of (text, metadata, distance)."""
        return self._store.search(question, k=k, where=where)

    def query_as_context(
        self,
        question: str,
        k: int = 5,
        max_chars: int = 3000,
        where: dict | None = None,
    ) -> str:
        """Return retrieved context as a formatted string for LLM injection."""
        results = self.query(question, k=k, where=where)
        if not results:
            return ""

        parts = ["[Retrieved context]"]
        total = 0
        for i, (text, meta, dist) in enumerate(results, 1):
            source = meta.get("source", "?")
            snippet = text.strip()
            if total + len(snippet) > max_chars:
                snippet = snippet[: max_chars - total] + "…"
            parts.append(f"[{i}] Source: {source}\n{snippet}")
            total += len(snippet)
            if total >= max_chars:
                break

        return "\n\n".join(parts)

    def stats(self) -> dict:
        backend = "in-memory" if self._store._fallback else "weaviate"
        return {
            "chunks":     self._store.count(),
            "collection": self._store.collection_name,
            "backend":    backend,
        }


# ── Singleton ─────────────────────────────────────────────────────────

_pipeline: RAGPipeline | None = None


def get_pipeline() -> RAGPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline
