"""Weaviate vector store for JARVIS RAG.

Persists documents in a Weaviate collection (JARVISDocuments).
Uses the same local sentence-transformers embeddings as the rest of JARVIS.

Configuration (env vars, same as memory/weaviate_backend.py):
  JARVIS_WEAVIATE_URL      — default: http://localhost:8080
  JARVIS_WEAVIATE_API_KEY  — optional (for Weaviate Cloud)
  JARVIS_WEAVIATE_GRPC_PORT — default: 50051

If Weaviate is unavailable, falls back to a simple in-memory store
that still supports search (slower, not persistent across restarts).
"""

import logging
import os
import hashlib
import time
from typing import List, Tuple, Optional

from .embeddings import get_embedder

log = logging.getLogger(__name__)

_DEFAULT_COLLECTION = "JARVISDocuments"

SearchResult = Tuple[str, dict, float]  # (text, metadata, score)


# ── In-memory fallback ────────────────────────────────────────────────

class _InMemoryFallback:
    """Simple cosine-similarity store used when Weaviate is not available.
    Not persistent — resets on restart. Fine for development/testing.
    """

    def __init__(self):
        self._docs: List[dict] = []  # {text, metadata, embedding}
        self._embedder = None

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def add(self, chunks: List[str], metadatas: List[dict] | None = None) -> int:
        metas = metadatas or [{} for _ in chunks]
        embedder = self._get_embedder()
        embeddings = embedder.embed_documents(chunks)
        for text, meta, emb in zip(chunks, metas, embeddings):
            doc_id = hashlib.md5(f"{meta.get('source','')}::{text[:64]}".encode()).hexdigest()
            # Upsert by id
            existing = next((i for i, d in enumerate(self._docs) if d["id"] == doc_id), None)
            entry = {"id": doc_id, "text": text, "meta": meta, "emb": emb}
            if existing is not None:
                self._docs[existing] = entry
            else:
                self._docs.append(entry)
        return len(chunks)

    def search(self, query: str, k: int = 5, where: dict | None = None) -> List[SearchResult]:
        if not self._docs:
            return []
        import numpy as np
        q_emb = self._get_embedder().embed_query(query)
        q = np.array(q_emb)
        scores = []
        for doc in self._docs:
            if where:
                src = doc["meta"].get("source", "")
                if not all(doc["meta"].get(k2) == v for k2, v in where.items()):
                    continue
            d = np.array(doc["emb"])
            # cosine similarity
            norm = (np.linalg.norm(q) * np.linalg.norm(d)) or 1e-9
            score = float(np.dot(q, d) / norm)
            scores.append((score, doc))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [(d["text"], d["meta"], 1.0 - s) for s, d in scores[:k]]

    def count(self) -> int:
        return len(self._docs)

    def delete_by_source(self, source: str) -> None:
        self._docs = [d for d in self._docs if d["meta"].get("source") != source]

    def clear(self) -> None:
        self._docs.clear()


# ── Weaviate store ────────────────────────────────────────────────────

class VectorStore:
    """RAG vector store backed by Weaviate (with in-memory fallback)."""

    def __init__(
        self,
        collection: str = _DEFAULT_COLLECTION,
        embed_model: str | None = None,
    ):
        self.collection_name = collection
        self.persist_dir = os.path.expanduser(
            os.environ.get("JARVIS_WEAVIATE_URL", "http://localhost:8080")
        )
        self._embedder = get_embedder(embed_model)
        self._client = None
        self._col = None
        self._fallback: Optional[_InMemoryFallback] = None
        self._init()

    def _init(self):
        try:
            import weaviate
            import weaviate.classes as wvc

            url = os.environ.get("JARVIS_WEAVIATE_URL", "http://localhost:8080")
            api_key = os.environ.get("JARVIS_WEAVIATE_API_KEY", "")
            grpc_port = int(os.environ.get("JARVIS_WEAVIATE_GRPC_PORT", "50051"))

            if api_key:
                self._client = weaviate.connect_to_weaviate_cloud(
                    cluster_url=url,
                    auth_credentials=weaviate.auth.AuthApiKey(api_key=api_key),
                )
            else:
                port = int(url.split(":")[-1]) if ":" in url and not url.endswith("8080") else 8080
                self._client = weaviate.connect_to_local(port=port, grpc_port=grpc_port)

            self._ensure_collection(wvc)
            self._col = self._client.collections.get(self.collection_name)
            log.info("RAG store: Weaviate collection '%s' ready.", self.collection_name)
        except ImportError:
            log.debug("weaviate-client not installed — using in-memory RAG fallback.")
            self._fallback = _InMemoryFallback()
        except Exception as e:
            log.info("Weaviate unavailable (%s) — using in-memory RAG fallback.", type(e).__name__)
            self._fallback = _InMemoryFallback()

    def _ensure_collection(self, wvc):
        if self._client.collections.exists(self.collection_name):
            return
        self._client.collections.create(
            name=self.collection_name,
            vector_config=wvc.config.Configure.Vectors.none(),
            properties=[
                wvc.config.Property(name="text",     data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="source",   data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="page",     data_type=wvc.config.DataType.INT),
                wvc.config.Property(name="doc_id",   data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="timestamp",data_type=wvc.config.DataType.NUMBER),
            ],
        )
        log.debug("Created Weaviate collection: %s", self.collection_name)

    # ── Ingest ────────────────────────────────────────────────────────

    def add(self, chunks: List[str], metadatas: List[dict] | None = None) -> int:
        if not chunks:
            return 0
        if self._fallback:
            return self._fallback.add(chunks, metadatas)

        metas = metadatas or [{} for _ in chunks]
        embeddings = self._embedder.embed_documents(chunks)

        with self._col.batch.dynamic() as batch:
            for text, meta, emb in zip(chunks, metas, embeddings):
                doc_id = hashlib.md5(
                    f"{meta.get('source', '')}::{text[:64]}".encode()
                ).hexdigest()
                props = {
                    "text":      text,
                    "source":    meta.get("source", ""),
                    "page":      int(meta.get("page", 0)),
                    "doc_id":    doc_id,
                    "timestamp": time.time(),
                }
                batch.add_object(properties=props, vector=emb)

        log.debug("Upserted %d chunks into %s", len(chunks), self.collection_name)
        return len(chunks)

    # ── Search ────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
    ) -> List[SearchResult]:
        if self._fallback:
            return self._fallback.search(query, k=k, where=where)

        q_emb = self._embedder.embed_query(query)

        try:
            import weaviate.classes as wvc
            filters = None
            if where and "source" in where:
                filters = wvc.query.Filter.by_property("source").equal(where["source"])

            results = self._col.query.near_vector(
                near_vector=q_emb,
                limit=k,
                return_properties=["text", "source", "page"],
                return_metadata=["certainty"],
                filters=filters,
            )
            out: List[SearchResult] = []
            for obj in results.objects:
                props = obj.properties
                certainty = obj.metadata.certainty if obj.metadata else 0.0
                meta = {"source": props.get("source", ""), "page": props.get("page", 0)}
                out.append((props.get("text", ""), meta, 1.0 - certainty))
            return out
        except Exception as e:
            log.warning("Weaviate search error: %s", e)
            return []

    # ── Housekeeping ──────────────────────────────────────────────────

    def count(self) -> int:
        if self._fallback:
            return self._fallback.count()
        try:
            return self._col.aggregate.over_all(total_count=True).total_count or 0
        except Exception:
            return 0

    def delete_by_source(self, source: str) -> None:
        if self._fallback:
            self._fallback.delete_by_source(source)
            return
        try:
            import weaviate.classes as wvc
            self._col.data.delete_many(
                where=wvc.query.Filter.by_property("source").equal(source)
            )
        except Exception as e:
            log.debug("delete_by_source error: %s", e)

    def clear(self) -> None:
        if self._fallback:
            self._fallback.clear()
            return
        try:
            self._client.collections.delete(self.collection_name)
            import weaviate.classes as wvc
            self._ensure_collection(wvc)
            self._col = self._client.collections.get(self.collection_name)
            log.warning("Collection '%s' cleared.", self.collection_name)
        except Exception as e:
            log.warning("clear() error: %s", e)

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __del__(self):
        self.close()


# ── Singleton ─────────────────────────────────────────────────────────

_store: VectorStore | None = None


def get_store(collection: str = _DEFAULT_COLLECTION) -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore(collection=collection)
    return _store
