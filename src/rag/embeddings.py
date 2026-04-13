"""Local sentence-transformers embedder — no API key required.

Model is downloaded once to ~/.cache/huggingface/ on first use (~90 MB).
Default: all-MiniLM-L6-v2 (384-dim, fast, good quality).
Override with env JARVIS_EMBED_MODEL.
"""

import logging
import os
from typing import List

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class LocalEmbeddings:
    """LangChain-compatible embeddings using sentence-transformers locally."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.environ.get("JARVIS_EMBED_MODEL", _DEFAULT_MODEL)
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                log.info("Loading embedding model: %s", self.model_name)
                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers not installed. Run: pip install sentence-transformers"
                )
        return self._model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        model = self._load()
        embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        return embeddings.tolist()

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


# Singleton
_embedder: LocalEmbeddings | None = None


def get_embedder(model_name: str | None = None) -> LocalEmbeddings:
    global _embedder
    if _embedder is None or (model_name and model_name != _embedder.model_name):
        _embedder = LocalEmbeddings(model_name)
    return _embedder
