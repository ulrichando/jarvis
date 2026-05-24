"""Web provider base + web_extract/web_crawl consumer-tool gating.

Backend-agnostic: exercises the JARVIS-native WebSearchProvider base, capability
resolution, and that the two new tools are inert until a capable provider is
registered. Per-backend availability tests live alongside each plugin port.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_base_capability_flags_default_false():
    from tools.web_providers import WebSearchProvider

    class P(WebSearchProvider):
        name = "p"

        def is_available(self):
            return True

    p = P()
    assert p.supports_search() is False
    assert p.supports_extract() is False
    assert p.supports_crawl() is False
    # Unimplemented capabilities return the failure shape, never raise.
    assert p.extract(["x"])["success"] is False


def test_web_extract_inert_without_provider():
    from tools import _provider_registry as pr
    from tools.web_providers import check_web_extract_available, check_web_crawl_available

    pr.reset_providers("web")
    assert check_web_extract_available() is False
    assert check_web_crawl_available() is False


def test_first_capable_resolves_registered_provider():
    from tools import _provider_registry as pr
    from tools.web_providers import WebSearchProvider, _first_capable, check_web_extract_available

    class Extractor(WebSearchProvider):
        name = "fake-extract"

        def is_available(self):
            return True

        def supports_extract(self):
            return True

        def extract(self, urls):
            return {"success": True, "data": [{"url": urls[0], "title": "t", "content": "body"}]}

    pr.reset_providers("web")
    pr.register_provider("web", "fake-extract", Extractor())
    assert check_web_extract_available() is True
    assert _first_capable("extract") is not None
    assert _first_capable("crawl") is None  # extractor doesn't advertise crawl
    pr.reset_providers("web")


def test_fmt_extract_caps_and_handles_empty():
    from tools.web_providers import _fmt_extract

    assert _fmt_extract([]) == "(no content extracted)"
    big = [{"url": "u", "title": "T", "content": "x" * 10_000}]
    rendered = _fmt_extract(big, cap=100)
    assert "[truncated]" in rendered
    assert rendered.startswith("# T")


def test_web_extract_and_crawl_tools_registered():
    """Both tools must be present in the registry (gated off without keys, but
    still registered so they light up the moment a backend key is set)."""
    import tools.web_providers  # noqa: F401  (ensures registration side effect)
    from tools.registry import registry

    names = set(registry.all_names())
    assert "web_extract" in names
    assert "web_crawl" in names


def test_web_extract_handles_async_provider():
    """An async-native provider's extract() must be awaited, not returned raw.

    Guards the parallel backend (async def extract) against the to_thread path
    that would otherwise hand back an un-awaited coroutine.
    """
    import asyncio

    from tools import _provider_registry as pr
    from tools.web_providers import WebSearchProvider, _handle_web_extract

    class AsyncExtractor(WebSearchProvider):
        name = "async-x"

        def is_available(self):
            return True

        def supports_extract(self):
            return True

        async def extract(self, urls):
            return {"success": True, "data": [{"url": urls[0], "title": "T", "content": "async body"}]}

    pr.reset_providers("web")
    pr.register_provider("web", "async-x", AsyncExtractor())
    try:
        out = asyncio.run(_handle_web_extract({"urls": ["http://x"]}))
        assert "async body" in out
    finally:
        pr.reset_providers("web")


def test_web_search_prefers_credentialed_provider():
    """With a search-capable web provider registered, web_search uses it (no DDG)."""
    import asyncio

    from tools import _provider_registry as pr
    from tools.web_providers import WebSearchProvider
    from tools.web_tools import _handle_web_search

    class SearchProv(WebSearchProvider):
        name = "fake-search"

        def is_available(self):
            return True

        def supports_search(self):
            return True

        def search(self, query, limit=5):
            return {
                "success": True,
                "data": {"web": [{"title": "Hit", "url": "http://hit", "description": "d", "position": 1}]},
            }

    pr.reset_providers("web")
    pr.register_provider("web", "fake-search", SearchProv())
    try:
        out = asyncio.run(_handle_web_search({"query": "anything", "limit": 3}))
        assert "Hit" in out and "http://hit" in out
    finally:
        pr.reset_providers("web")
