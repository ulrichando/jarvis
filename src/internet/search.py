"""JARVIS Internet Search — find information from the web."""

import warnings
import logging
import os
import sys

# Suppress ALL warnings from search libraries
warnings.filterwarnings("ignore")
logging.getLogger("primp").setLevel(logging.CRITICAL)
logging.getLogger("duckduckgo_search").setLevel(logging.CRITICAL)

try:
    from ddgs import DDGS
except ImportError:
    try:
        # Suppress the rename warning during import
        _stderr = sys.stderr
        sys.stderr = open(os.devnull, "w")
        try:
            from duckduckgo_search import DDGS
        finally:
            sys.stderr.close()
            sys.stderr = _stderr
    except ImportError:
        DDGS = None


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web using DuckDuckGo. Returns list of {title, url, body}."""
    if DDGS is None:
        return [{"title": "Search unavailable", "url": "", "body": "Install: pip install ddgs"}]
    try:
        results = DDGS().text(query, max_results=max_results)
        return [{"title": r.get("title", ""), "url": r.get("href", r.get("url", "")), "body": r.get("body", "")} for r in results]
    except Exception as e:
        return [{"title": "Search failed", "url": "", "body": str(e)}]


def news_search(query: str, max_results: int = 5) -> list[dict]:
    """Search recent news."""
    if DDGS is None:
        return []
    try:
        _stderr = sys.stderr
        sys.stderr = open(os.devnull, "w")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.news(query, max_results=max_results))
        finally:
            sys.stderr.close()
            sys.stderr = _stderr
        return [{"title": r["title"], "url": r["url"], "body": r["body"]} for r in results]
    except Exception:
        return []
