"""JARVIS RAG — Retrieval-Augmented Generation pipeline.

Components:
  embeddings  — local sentence-transformers embedder (no API key)
  splitter    — LangChain recursive text splitter with sensible defaults
  loaders     — document loaders for PDF, web pages, plain text, code
  store       — ChromaDB vector store (persisted in ~/.jarvis/rag/)
  pipeline    — unified ingest + query API consumed by the rag_search tool
"""

from .pipeline import RAGPipeline, get_pipeline  # noqa: F401
