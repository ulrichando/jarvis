"""Weaviate backend for JARVIS long-term semantic memory.

Adds vector-based semantic search on top of the NeuralLattice.
Works as an additional recall layer — lattice remains authoritative.

Configuration (env vars):
  JARVIS_WEAVIATE_URL      — default: http://localhost:8080
  JARVIS_WEAVIATE_API_KEY  — optional (for Weaviate Cloud)
  JARVIS_WEAVIATE_GRPC_PORT — default: 50051 (for local v4 client)

Collection name: JARVISMemory
Each object stores: content, node_type, tags, strength, timestamp.
Embedding model: same as RAG (all-MiniLM-L6-v2) for consistency.

Falls back silently if Weaviate is not running.
"""

import logging
import os
import time
from typing import Any

log = logging.getLogger("jarvis.memory.weaviate")

_DEFAULT_URL  = "http://localhost:8080"
_COLLECTION   = "JARVISMemory"
_EMBED_DIM    = 384  # all-MiniLM-L6-v2 output dim


class WeaviateMemory:
    """Semantic long-term memory backed by Weaviate.

    All methods are best-effort — failures are logged and ignored
    so JARVIS still works without Weaviate.
    """

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
    ):
        self._url = url or os.environ.get("JARVIS_WEAVIATE_URL", _DEFAULT_URL)
        self._api_key = api_key or os.environ.get("JARVIS_WEAVIATE_API_KEY", "")
        self._client = None
        self._available = False
        self._embedder = None
        self._init()

    # ── Initialise ────────────────────────────────────────────────────

    def _init(self):
        try:
            import weaviate
            import weaviate.classes as wvc

            if self._api_key:
                auth = weaviate.auth.AuthApiKey(api_key=self._api_key)
                self._client = weaviate.connect_to_weaviate_cloud(
                    cluster_url=self._url,
                    auth_credentials=auth,
                )
            else:
                # Local instance
                grpc_port = int(os.environ.get("JARVIS_WEAVIATE_GRPC_PORT", "50051"))
                self._client = weaviate.connect_to_local(
                    port=int(self._url.split(":")[-1]) if ":" in self._url else 8080,
                    grpc_port=grpc_port,
                )

            # Ensure collection exists
            self._ensure_collection(wvc)
            self._available = True
            log.info("Weaviate semantic memory connected: %s", self._url)
        except ImportError:
            log.debug("weaviate-client not installed — Weaviate memory unavailable.")
        except Exception as e:
            log.info("Weaviate unavailable (%s) — using lattice only.", type(e).__name__)

    def _ensure_collection(self, wvc):
        """Create JARVISMemory collection if it doesn't exist."""
        if self._client.collections.exists(_COLLECTION):
            return
        self._client.collections.create(
            name=_COLLECTION,
            vector_config=wvc.config.Configure.Vectors.none(),
            properties=[
                wvc.config.Property(name="content",    data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="node_type",  data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="tags",       data_type=wvc.config.DataType.TEXT_ARRAY),
                wvc.config.Property(name="strength",   data_type=wvc.config.DataType.NUMBER),
                wvc.config.Property(name="timestamp",  data_type=wvc.config.DataType.NUMBER),
            ],
        )
        log.debug("Created Weaviate collection: %s", _COLLECTION)

    def _get_embedder(self):
        if self._embedder is None:
            from src.rag.embeddings import get_embedder
            self._embedder = get_embedder()
        return self._embedder

    def _embed(self, text: str) -> list[float]:
        return self._get_embedder().embed_query(text)

    # ── Store ─────────────────────────────────────────────────────────

    def store(self, content: str, node_type: str = "fact", tags: list[str] | None = None, strength: float = 1.0) -> bool:
        """Store a memory node in Weaviate. Returns True on success."""
        if not self._available:
            return False
        try:
            collection = self._client.collections.get(_COLLECTION)
            vector = self._embed(content)
            collection.data.insert(
                properties={
                    "content":   content,
                    "node_type": node_type,
                    "tags":      tags or [],
                    "strength":  strength,
                    "timestamp": time.time(),
                },
                vector=vector,
            )
            return True
        except Exception as e:
            log.debug("Weaviate store error: %s", e)
            return False

    # ── Recall ────────────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        top_k: int = 5,
        min_certainty: float = 0.6,
    ) -> list[dict]:
        """Semantic recall. Returns list of {content, node_type, tags, strength, score}."""
        if not self._available:
            return []
        try:
            collection = self._client.collections.get(_COLLECTION)
            query_vector = self._embed(query)
            results = collection.query.near_vector(
                near_vector=query_vector,
                limit=top_k,
                certainty=min_certainty,
                return_properties=["content", "node_type", "tags", "strength"],
                return_metadata=["certainty"],
            )
            out = []
            for obj in results.objects:
                props = obj.properties
                certainty = obj.metadata.certainty if obj.metadata else 0.0
                out.append({
                    "content":   props.get("content", ""),
                    "node_type": props.get("node_type", "fact"),
                    "tags":      props.get("tags", []),
                    "strength":  props.get("strength", 1.0),
                    "score":     certainty,
                })
            return out
        except Exception as e:
            log.debug("Weaviate recall error: %s", e)
            return []

    # ── Housekeeping ──────────────────────────────────────────────────

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __del__(self):
        self.close()

    @property
    def available(self) -> bool:
        return self._available


# ── Singleton ─────────────────────────────────────────────────────────

_weaviate: WeaviateMemory | None = None


def get_weaviate() -> WeaviateMemory:
    global _weaviate
    if _weaviate is None:
        _weaviate = WeaviateMemory()
    return _weaviate
