"""JARVIS Codebase Indexer — two-tier context injection.

Tier 1: Always-fresh directory tree (os.walk + pathspec, ~50ms, zero staleness).
Tier 2: Per-file symbol/summary cache with mtime+size invalidation.

Usage:
    from src.indexer.builder import get_context, build_index

    # Inject into session context (called by PromptBuilder)
    context_md = get_context(root=Path.cwd(), max_chars=10000)

    # Pre-build full symbol index (called by /index build command)
    stats = build_index(root=Path.cwd())
"""
from src.indexer.builder import get_context, build_index, get_status

__all__ = ["get_context", "build_index", "get_status"]
