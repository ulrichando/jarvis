# Hermes Plugin Port Implementation Plan

> **⚠️ PARTIALLY OBSOLETE (2026-06-23).** The 48 *inert* mirror plugins this plan
> created — 29 `model-providers`, 5 `platforms`, the 7 stub `memory` backends, and
> `context_engine`/`disk-cleanup`/`teams_pipeline`/`observability`/`achievements`/
> `kanban`/`example-dashboard` — were **removed** (no consumer; redundant with
> `providers/llm.py` for LLMs and honcho for memory). The "the user wants them
> present" rationale no longer holds. What REMAINS functional + live: the 4
> capability plugins (`web`, `image_gen`, `video_gen`, `browser`), `memory/honcho`,
> plus `spotify`/`google_meet` and the `example` test fixture.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port all 16 Hermes plugins into `src/voice-agent/plugins/` as JARVIS-native plugins — the 4 capability-adders (`web`, `image_gen`, `video_gen`, `browser`) and `memory` made functional, the other 9 subsystem/dashboard plugins present as honest 1:1 mirrors that import cleanly but are inert in JARVIS's voice substrate.

**Architecture:** Preserve Hermes's exact plugin *structure* (`plugins/<cap>/<backend>/{plugin.yaml, __init__.py, provider.py}`, `kind: backend`, `register(ctx)` → `ctx.register_<cap>_provider(provider)`), but adapt it to JARVIS's substrate: wire the currently-no-op `PluginContext.register_*_provider` hooks into the existing generic `tools/_provider_registry.py` (kinds `image`/`web`/`video`/`browser`); port each Hermes `provider.py`'s *logic* into a JARVIS-native provider class (NO `agent.*` imports — that package doesn't exist in voice-agent); add consumer registry tools (`web_extract`/`web_crawl`; `image_generate`/`video_generate` already resolve their kind from the registry). Memory is wired into JARVIS's file-backed memory or, if it doesn't graft, restructured per the user's go-ahead.

**Tech Stack:** Python 3.13, LiveKit Agents 1.5.9, `tools.registry` + `tools._adapter` (RawFunctionTool adapter), `tools._provider_registry` (kind→{name→provider}), `tools.plugin_system` (PluginManager/PluginContext, 2-level category discovery), httpx/requests, optional vendor SDKs (exa-py, fal-client) lazy-imported.

**Naming:** JARVIS-native everywhere. Zero `hermes`/`Hermes`/`HERMES` tokens in any ported file. `hermes-achievements` → `achievements`. No upstream brand tokens in comments or docstrings beyond neutral "ported from the upstream X".

**Hard invariants (must hold after every task):**
- `tests/test_no_duplicate_tools.py` green — no duplicate tool names (LiveKit `ToolContext.flatten()` crashes session start otherwise).
- `from tools._adapter import load_all_livekit_tools; load_all_livekit_tools()` imports cleanly (this calls `discover_plugins()`).
- `import jarvis_agent` succeeds (the 4 monkeypatches still install).
- Full suite green: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q`.
- Credentialed providers are INERT without their key (gated via `is_available()`), exactly like the existing `image_generate` tool.

**Out of scope (do NOT touch):** `src/cli/` (separate functional codebase), `src/voice-agent/desktop-tauri/`, `src/web/`, and `jarvis_agent.py`'s turn loop / barge-in / TTS path. Memory work may touch `pipeline/file_memory.py` and (if restructure is needed) the `_build_memory_block()` wiring only — surface that before doing it.

---

## Reference: the established JARVIS pattern (read before any task)

- `tools/_provider_registry.py` — generic registry. A provider is any object with `name: str` + `is_available() -> bool` + capability methods. `register_provider(kind, name, provider)`, `get_provider(kind, name=None)` (name omitted → first available), `has_available_provider(kind)`.
- `tools/image_gen.py` — the reference consumer+provider file. Providers are plain classes (`OpenAIImageGenProvider`, `XAIImageGenProvider`) self-registered at import via `provider_registry.register_provider("image", name, inst)`. The tool resolves a provider with `get_provider("image")`, calls `.generate(...)`, gates with `check_fn=has_available_provider`.
- `tools/web_tools.py` — existing keyless `web_search`/`web_fetch` (DuckDuckGo). Always available. KEEP these; the port adds credentialed providers + new `web_extract`/`web_crawl` tools alongside.
- `tools/plugin_system.py` — `PluginContext.register_tool` is the only wired hook; `register_web_search_provider`/`register_image_gen_provider`/`register_browser_provider`/`register_memory_provider` are no-op `_stub` calls; there is NO `register_video_gen_provider` yet. 2-level discovery already supports `plugins/web/tavily/plugin.yaml` (key `web/tavily`).
- Hermes `WebSearchProvider` ABC response shapes (preserve bit-for-bit so the tool wrapper is thin):
  - search → `{"success": True, "data": {"web": [{"title","url","description","position"}]}}`
  - extract → `{"success": True, "data": [{"url","title","content","raw_content","metadata"}]}`
  - crawl → `{"success": True, "data": [...]}` (same item shape as extract)
  - failure → `{"success": False, "error": str}`

---

## Task 0: Remove the wrong stub dirs

The blocked subagent left 7 content dirs whose `__init__.py` files falsely claim "No-op: dup of …" (the exact assumption we're correcting) plus 7 empty dirs. All are uncommitted. Wipe them so the port starts clean.

**Files:**
- Delete: `src/voice-agent/plugins/{browser,image_gen,memory,model-providers,platforms,video_gen,web}/` (subagent content dirs)
- Delete: `src/voice-agent/plugins/{context_engine,disk-cleanup,example-dashboard,kanban,observability,teams_pipeline,achievements}/` (empty shells)
- Keep untouched: `src/voice-agent/plugins/{example,google_meet,spotify}/`

- [ ] **Step 1: Confirm all target dirs are untracked or empty (no committed content lost)**

Run: `cd src/voice-agent && git status --short plugins/ && for d in context_engine disk-cleanup example-dashboard kanban observability teams_pipeline achievements; do echo "$d: $(find plugins/$d -type f 2>/dev/null | wc -l) files"; done`
Expected: the 7 content dirs show as `??` (untracked); the 7 shells show `0 files`.

- [ ] **Step 2: Remove them**

```bash
cd src/voice-agent/plugins
rm -rf browser image_gen memory model-providers platforms video_gen web \
       context_engine disk-cleanup example-dashboard kanban observability teams_pipeline achievements
```

- [ ] **Step 3: Verify only the 3 originals remain**

Run: `ls -1 src/voice-agent/plugins/`
Expected: `example  google_meet  spotify`

- [ ] **Step 4: Commit**

```bash
cd src/voice-agent && git add -A plugins/ && git commit -m "chore(plugins): remove wrong-approach no-op stub dirs before real port"
```

---

## Task 1: Foundation — wire provider hooks + add `video` kind support

Make `PluginContext.register_*_provider` route into `_provider_registry` instead of no-op, and add the missing `register_video_gen_provider`. This is what turns a Hermes-shaped backend plugin into a functional JARVIS provider.

**Files:**
- Modify: `src/voice-agent/tools/plugin_system.py` (the 4 provider stubs → real wiring; add a 5th)
- Test: `src/voice-agent/tests/test_plugin_provider_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugin_provider_wiring.py
"""PluginContext provider-registration hooks must route into _provider_registry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class _FakeProvider:
    def __init__(self, name): self.name = name
    def is_available(self): return True


def _ctx():
    from tools.plugin_system import PluginContext, PluginManager, PluginManifest
    mgr = PluginManager()
    return PluginContext(PluginManifest(name="t"), mgr)


def test_register_web_search_provider_lands_in_registry():
    from tools import _provider_registry as pr
    pr.reset_providers("web")
    _ctx().register_web_search_provider(_FakeProvider("exa"))
    assert pr.get_provider("web", "exa") is not None
    pr.reset_providers("web")


def test_register_video_gen_provider_lands_in_registry():
    from tools import _provider_registry as pr
    pr.reset_providers("video")
    _ctx().register_video_gen_provider(_FakeProvider("fal"))
    assert pr.get_provider("video", "fal") is not None
    pr.reset_providers("video")


def test_register_image_and_browser_providers_land_in_registry():
    from tools import _provider_registry as pr
    pr.reset_providers("image"); pr.reset_providers("browser")
    _ctx().register_image_gen_provider(_FakeProvider("codex"))
    _ctx().register_browser_provider(_FakeProvider("browserbase"))
    assert pr.get_provider("image", "codex") is not None
    assert pr.get_provider("browser", "browserbase") is not None


def test_provider_without_name_is_skipped_gracefully():
    """A provider lacking a usable name must not raise out of register()."""
    class NoName:
        is_available = lambda self: True
    _ctx().register_web_search_provider(NoName())  # logs + returns, no raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_plugin_provider_wiring.py -q`
Expected: FAIL (`get_provider` returns None — stubs are no-ops; `register_video_gen_provider` AttributeError).

- [ ] **Step 3: Implement the wiring**

Replace the four provider `_stub` methods in `tools/plugin_system.py` and add the video one. Each routes to the registry under the right kind, derives the name from `provider.name`, and never raises out (a bad provider must not break discovery):

```python
    # -- provider registration (wired into tools._provider_registry) --------
    #
    # Hermes-shaped backend plugins register a provider object (duck-typed
    # name + is_available() + capability methods) under a capability *kind*.
    # The consuming registry tool (image_generate / video_generate /
    # web_extract / browser_*) resolves it via _provider_registry.get_provider.

    def _register_provider(self, kind: str, provider: Any) -> None:
        name = str(getattr(provider, "name", "") or "").strip()
        if not name:
            logger.warning(
                "Plugin %s registered a %s provider with no usable .name — skipped",
                self.manifest.name, kind,
            )
            return
        from tools import _provider_registry
        _provider_registry.register_provider(kind, name, provider)
        logger.debug("Plugin %s registered %s provider %r", self.manifest.name, kind, name)

    def register_image_gen_provider(self, provider: Any) -> None:
        self._register_provider("image", provider)

    def register_web_search_provider(self, provider: Any) -> None:
        self._register_provider("web", provider)

    def register_video_gen_provider(self, provider: Any) -> None:
        self._register_provider("video", provider)

    def register_browser_provider(self, provider: Any) -> None:
        self._register_provider("browser", provider)
```

Leave `register_memory_provider`, `register_context_engine`, `register_platform`, `register_hook`, `register_skill`, `register_cli_command`, `register_command` as no-op stubs (Task 6 handles memory separately; the rest stay inert by design).

- [ ] **Step 4: Run to verify pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_plugin_provider_wiring.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Guard test still green**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_no_duplicate_tools.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd src/voice-agent && git add tools/plugin_system.py tests/test_plugin_provider_wiring.py
git commit -m "feat(plugins): wire register_*_provider hooks into provider registry"
```

---

## Task 2: `web` plugin — 7 credentialed backends + `web_extract`/`web_crawl` tools

Port Hermes's `plugins/web/<backend>/` providers. JARVIS keeps keyless `web_search`/`web_fetch` (Task does NOT touch them). Add: a JARVIS-native `WebSearchProvider` base, the backend providers, and two new consumer tools (`web_extract`, `web_crawl`) that resolve a `web`-kind provider.

**Backends to port** (each → `plugins/web/<name>/{plugin.yaml, __init__.py, provider.py}`, `kind: backend`): `tavily` (search+extract+crawl), `exa` (search+extract), `firecrawl` (search+extract+crawl), `brave_free` (search), `parallel` (search+extract), `searxng` (search, self-hosted), `xai` (search). Skip `ddgs` (keyless — duplicates the built-in `web_search`; do not register a `web_search` tool name from it).

**Files:**
- Create: `src/voice-agent/tools/web_providers.py` (JARVIS-native `WebSearchProvider` base + `register_web_provider` helper + the `web_extract`/`web_crawl` registry tools)
- Create: `src/voice-agent/plugins/web/<name>/{plugin.yaml,__init__.py,provider.py}` for the 7 backends
- Test: `src/voice-agent/tests/test_web_providers.py`

- [ ] **Step 1: Write the failing test (base + tool gating + one provider, no network)**

```python
# tests/test_web_providers.py
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_base_capability_flags_default_false():
    from tools.web_providers import WebSearchProvider
    class P(WebSearchProvider):
        name = "p"
        def is_available(self): return True
    p = P()
    assert p.supports_search() is False
    assert p.supports_extract() is False
    assert p.supports_crawl() is False


def test_web_extract_tool_inert_without_provider():
    """web_extract gates off when no extract-capable web provider is available."""
    from tools import _provider_registry as pr
    pr.reset_providers("web")
    from tools.web_providers import check_web_extract_available
    assert check_web_extract_available() is False


def test_tavily_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    # Import the ported provider class directly from the plugin module file.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_t_tavily",
        Path(__file__).parent.parent / "plugins/web/tavily/provider.py",
    )
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    prov = mod.TavilyWebSearchProvider()
    assert prov.name == "tavily"
    assert prov.supports_crawl() is True
    assert prov.is_available() is False  # no key
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_web_providers.py -q`
Expected: FAIL (`tools.web_providers` missing).

- [ ] **Step 3: Write `tools/web_providers.py`**

Define the base + the two consumer tools. The base mirrors the Hermes ABC but is JARVIS-native and duck-type-compatible with `_provider_registry`:

```python
"""JARVIS-native web provider base + web_extract/web_crawl consumer tools.

Ported from the upstream web_search_provider ABC + web_extract/web_crawl
dispatchers. The keyless web_search/web_fetch tools in tools/web_tools.py are
unchanged; this module adds credentialed providers (kind="web") and the two
extra capabilities the keyless backend lacks: markdown extract + deep crawl.
"""
from __future__ import annotations
import abc, asyncio, logging
from typing import Any, Dict, List
from . import _provider_registry as provider_registry
from .registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)
PROVIDER_KIND = "web"


class WebSearchProvider(abc.ABC):
    name: str = ""
    @abc.abstractmethod
    def is_available(self) -> bool: ...
    def supports_search(self) -> bool: return False
    def supports_extract(self) -> bool: return False
    def supports_crawl(self) -> bool: return False
    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        return {"success": False, "error": f"{self.name} does not support search"}
    def extract(self, urls: List[str]) -> Dict[str, Any]:
        return {"success": False, "error": f"{self.name} does not support extract"}
    def crawl(self, url: str, **kw: Any) -> Dict[str, Any]:
        return {"success": False, "error": f"{self.name} does not support crawl"}


def _first_capable(capability: str):
    for p in provider_registry.available_providers(PROVIDER_KIND):
        if getattr(p, f"supports_{capability}", lambda: False)():
            return p
    return None


def check_web_extract_available() -> bool:
    return _first_capable("extract") is not None


def check_web_crawl_available() -> bool:
    return _first_capable("crawl") is not None


def _fmt_extract(data: List[Dict[str, Any]], cap: int = 6000) -> str:
    out = []
    for item in data:
        body = (item.get("content") or item.get("raw_content") or "").strip()
        if len(body) > cap:
            body = body[:cap] + "… [truncated]"
        out.append(f"# {item.get('title') or item.get('url')}\n{item.get('url')}\n\n{body}")
    return "\n\n---\n\n".join(out) if out else "(no content extracted)"


async def _handle_web_extract(args: dict) -> str:
    raw = args.get("urls") or args.get("url")
    urls = [raw] if isinstance(raw, str) else list(raw or [])
    urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
    if not urls:
        return tool_error("web_extract requires a url or urls list")
    prov = _first_capable("extract")
    if prov is None:
        return tool_error("No extract-capable web backend available. Set TAVILY_API_KEY / EXA_API_KEY / FIRECRAWL_API_KEY / PARALLEL_API_KEY.")
    try:
        res = await asyncio.to_thread(prov.extract, urls)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"web_extract failed: {exc}")
    if not res.get("success"):
        return tool_error(res.get("error", "extract failed"), provider=prov.name)
    return _fmt_extract(res.get("data") or [])


async def _handle_web_crawl(args: dict) -> str:
    url = (args.get("url") or "").strip()
    if not url:
        return tool_error("web_crawl requires a url")
    prov = _first_capable("crawl")
    if prov is None:
        return tool_error("No crawl-capable web backend available. Set TAVILY_API_KEY or FIRECRAWL_API_KEY.")
    try:
        res = await asyncio.to_thread(prov.crawl, url)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"web_crawl failed: {exc}")
    if not res.get("success"):
        return tool_error(res.get("error", "crawl failed"), provider=prov.name)
    return _fmt_extract(res.get("data") or [])


registry.register(
    name="web_extract",
    schema={"name": "web_extract", "description":
            "Extract clean readable content (markdown) from one or more URLs using a credentialed backend (Tavily/Exa/Firecrawl/Parallel). Use when you need the FULL content of known pages, not a search. For a quick single-page text grab with no API key, use web_fetch instead.",
            "parameters": {"type": "object", "properties": {
                "urls": {"type": "array", "items": {"type": "string"},
                         "description": "URLs to extract content from."}},
                "required": ["urls"]}},
    handler=_handle_web_extract, toolset="web", check_fn=check_web_extract_available,
    is_async=True, emoji="📄", max_result_size_chars=20_000,
)
registry.register(
    name="web_crawl",
    schema={"name": "web_crawl", "description":
            "Deep-crawl a site from a seed URL and return aggregated content. Credentialed backend (Tavily/Firecrawl). Use for 'read everything under <site>' research; for a single page use web_extract/web_fetch.",
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "description": "Seed URL to crawl."}},
                "required": ["url"]}},
    handler=_handle_web_crawl, toolset="web", check_fn=check_web_crawl_available,
    is_async=True, emoji="🕸️", max_result_size_chars=20_000,
)
```

Add `from . import web_providers  # noqa: F401` to wherever the adapter imports built-in tool modules (`tools/_adapter.py::discover_builtin_tools` — match how `web_tools`/`image_gen` are imported).

- [ ] **Step 4: Port each backend provider.py + plugin.yaml + __init__.py**

For each of the 7 backends, read `hermes/plugins/web/<name>/provider.py` and translate to JARVIS:
- Subclass `tools.web_providers.WebSearchProvider`.
- Replace `from agent.web_search_provider import WebSearchProvider` → `from tools.web_providers import WebSearchProvider`.
- Drop `tools.lazy_deps` usage → plain `try/except ImportError` around the vendor SDK import; gate `is_available()` on the env key AND importability.
- Drop all `config.yaml` reads; env-var driven only.
- Preserve the response shapes exactly.
- No `hermes` brand tokens (the Exa client header `x-exa-integration` value → `"jarvis-agent"`).

`plugin.yaml` shape (example for tavily):
```yaml
name: web-tavily
version: "1.0.0"
description: "Tavily web search + extract + crawl backend"
kind: backend
requires_env: [TAVILY_API_KEY]
```

`__init__.py` shape:
```python
"""Tavily web backend — registers a credentialed search+extract+crawl provider."""
from __future__ import annotations
from jarvis_plugins.web__tavily.provider import TavilyWebSearchProvider  # see note


def register(ctx) -> None:
    ctx.register_web_search_provider(TavilyWebSearchProvider())
```

NOTE on the provider import: the plugin loads as module `jarvis_plugins.web__tavily` with `__path__` set to the plugin dir (see `plugin_system._import_plugin_module`), so a sibling import is `from provider import TavilyWebSearchProvider` won't work and absolute `jarvis_plugins.web__tavily.provider` is fragile. SIMPLEST robust approach: put the provider class directly in `__init__.py` (no separate `provider.py`), OR load the sibling explicitly:
```python
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    __name__ + ".provider", pathlib.Path(__file__).parent / "provider.py")
_mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_mod)
TavilyWebSearchProvider = _mod.TavilyWebSearchProvider
```
Prefer putting the class in `__init__.py` directly to keep it simple — the separate `provider.py` is a Hermes convention we don't need. Keep one file per backend.

- [ ] **Step 5: Run tests**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_web_providers.py tests/test_no_duplicate_tools.py -q`
Expected: PASS. `web_extract`/`web_crawl` register but are filtered out (inert) when no key set.

- [ ] **Step 6: Verify discovery loads all 7 web backends without error**

Run: `cd src/voice-agent && .venv/bin/python -c "from tools.plugin_system import discover_plugins; m=discover_plugins(force=True); [print(p['key'], p['enabled'], p['error']) for p in m.list_plugins() if p['key'].startswith('web/')]"`
Expected: 7 rows, all `enabled=True`, `error=None`.

- [ ] **Step 7: Commit**

```bash
cd src/voice-agent && git add tools/web_providers.py tools/_adapter.py plugins/web/ tests/test_web_providers.py
git commit -m "feat(plugins): port web search/extract/crawl backends (7 providers) + web_extract/web_crawl tools"
```

---

## Task 3: `video_gen` plugin — add FAL backend (xAI already covered)

JARVIS's `tools/video_gen.py` already ships an xAI `video_generate`. Confirm it resolves the `video` provider-kind; port Hermes's FAL provider as a `video`-kind backend so `video_generate` gains FAL (6 model families).

**Files:**
- Modify (if needed): `src/voice-agent/tools/video_gen.py` (ensure it resolves `get_provider("video")` and gates on `has_available_provider("video")` — match `image_gen.py`. If it currently hardcodes xAI, refactor xAI into a `video`-kind provider first.)
- Create: `src/voice-agent/plugins/video_gen/fal/{plugin.yaml,__init__.py}` (provider class inline)
- Test: `src/voice-agent/tests/test_video_providers.py`

- [ ] **Step 1: Read `tools/video_gen.py`** to see whether xAI is already a registered `video`-kind provider or hardcoded. If hardcoded, the first sub-step is to refactor it into an `XAIVideoGenProvider` registered via `provider_registry.register_provider("video", "xai", ...)`, with the tool resolving `get_provider("video")` — mirror `image_gen.py` exactly.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_video_providers.py
import sys
from pathlib import Path
import importlib.util
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_fal_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    spec = importlib.util.spec_from_file_location(
        "_t_fal", Path(__file__).parent.parent / "plugins/video_gen/fal/__init__.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    prov = mod.FALVideoGenProvider()
    assert prov.name == "fal"
    assert prov.is_available() is False


def test_video_generate_resolves_video_kind():
    """video_generate must consume the 'video' provider kind, not a hardcoded backend."""
    import tools.video_gen as vg
    assert "video" in (getattr(vg, "PROVIDER_KIND", ""),) or hasattr(vg, "PROVIDER_KIND")
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_video_providers.py -q`
Expected: FAIL (FAL plugin module missing).

- [ ] **Step 4: Port the FAL provider** from `hermes/plugins/video_gen/fal/provider.py`. Class `FALVideoGenProvider` with `name="fal"`, `is_available()` (gate on `FAL_KEY`/`FAL_API_KEY` + `fal_client` importable), `generate(prompt, ...)` returning the same dict shape `tools/video_gen.py` expects (read it to confirm — likely `{"success", "video"/"path", "model", "provider"}`). `register(ctx)` calls `ctx.register_video_gen_provider(FALVideoGenProvider())`. JARVIS-native; no brand tokens.

- [ ] **Step 5: Run tests + discovery**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_video_providers.py tests/test_no_duplicate_tools.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd src/voice-agent && git add tools/video_gen.py plugins/video_gen/ tests/test_video_providers.py
git commit -m "feat(plugins): port FAL video backend into video_generate provider kind"
```

---

## Task 4: `image_gen` plugin — add Codex OAuth backend (OpenAI + xAI already in tools/image_gen.py)

JARVIS's `tools/image_gen.py` already ships OpenAI gpt-image-2 (low/medium/high) + xAI. The only genuine addition is the `openai-codex` OAuth-auth backend. Port it as an `image`-kind provider; if Codex OAuth isn't configured, it's inert (so this is low-risk and small).

**Files:**
- Create: `src/voice-agent/plugins/image_gen/codex/{plugin.yaml,__init__.py}` (provider class inline; name `openai-codex`)
- Test: `src/voice-agent/tests/test_image_codex_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_image_codex_provider.py
import sys, importlib.util
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_codex_provider_unavailable_without_oauth(monkeypatch):
    monkeypatch.delenv("OPENAI_CODEX_OAUTH_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location(
        "_t_codex", Path(__file__).parent.parent / "plugins/image_gen/codex/__init__.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    prov = mod.CodexImageGenProvider()
    assert prov.name == "openai-codex"
    assert prov.is_available() is False
```

- [ ] **Step 2: Run to verify fails** — `pytest tests/test_image_codex_provider.py -q` → FAIL (module missing).

- [ ] **Step 3: Port** `hermes/plugins/image_gen/openai-codex/provider.py` → `CodexImageGenProvider` reusing `tools.image_gen`'s `save_b64_image`/`success_response`/`error_response` helpers. `register(ctx)` → `ctx.register_image_gen_provider(...)`. Gate on whatever Codex OAuth env/token Hermes uses (read its `is_available`).

- [ ] **Step 4: Tests + discovery** — `pytest tests/test_image_codex_provider.py tests/test_no_duplicate_tools.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add plugins/image_gen/ tests/test_image_codex_provider.py && git commit -m "feat(plugins): port OpenAI-Codex OAuth image backend"`

---

## Task 5: `browser` plugin — Browserbase + Firecrawl backends

JARVIS's `tools/browser.py` runs `browser_task` via an isolated `browser_use` venv. Port Hermes's `browserbase` (cloud browser, stealth/proxy) and `firecrawl` (web-extraction-as-browser) as `browser`-kind providers. The existing `browser_use` path stays the default. Read `tools/browser.py` first to decide the cleanest seam (likely: register the existing path as `browser`-kind `browser_use` provider, then add the two cloud providers; `browser_task` picks `get_provider("browser")` by configured name, default browser_use).

**Files:**
- Modify: `src/voice-agent/tools/browser.py` (resolve `browser`-kind provider; keep browser_use default)
- Create: `src/voice-agent/plugins/browser/{browserbase,firecrawl}/{plugin.yaml,__init__.py}`
- Test: `src/voice-agent/tests/test_browser_providers.py`

- [ ] **Step 1: Read `tools/browser.py`** — understand how `browser_task` currently runs, and what a provider needs to expose (likely an async `run_task(task: str) -> dict`).

- [ ] **Step 2: Write the failing test** (providers inert without keys):

```python
# tests/test_browser_providers.py
import sys, importlib.util
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"_t_{name}", Path(__file__).parent.parent / f"plugins/browser/{name}/__init__.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def test_browserbase_inert_without_key(monkeypatch):
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    prov = _load("browserbase").BrowserbaseProvider()
    assert prov.name == "browserbase"
    assert prov.is_available() is False


def test_firecrawl_browser_inert_without_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    prov = _load("firecrawl").FirecrawlBrowserProvider()
    assert prov.name == "firecrawl"
    assert prov.is_available() is False
```

- [ ] **Step 3: Run to verify fails** — FAIL (modules missing).

- [ ] **Step 4: Port** both providers from `hermes/plugins/browser/{browserbase,firecrawl}/provider.py`, adapting to whatever interface `tools/browser.py` consumes; gate on `BROWSERBASE_API_KEY` / `FIRECRAWL_API_KEY`. `register(ctx)` → `ctx.register_browser_provider(...)`. JARVIS-native.

- [ ] **Step 5: Tests + discovery** — `pytest tests/test_browser_providers.py tests/test_no_duplicate_tools.py -q` → PASS.

- [ ] **Step 6: Commit** — `git add tools/browser.py plugins/browser/ tests/test_browser_providers.py && git commit -m "feat(plugins): port Browserbase + Firecrawl browser backends"`

---

## Task 6: `memory` plugin — port the MemoryProvider abstraction + Honcho (flagship), wire to JARVIS

User explicitly named memory as a relevant subsystem and authorized restructuring JARVIS memory if Hermes's doesn't graft. JARVIS currently uses file-backed memory (`pipeline/file_memory.py`, single `memory(action,target)` tool). Hermes's `memory` plugin is 8 cloud `MemoryProvider` backends behind `register_memory_provider`.

**Approach (incremental, low-risk first):**
1. Port the `MemoryProvider` interface + Honcho backend present-and-discoverable, wired into `_provider_registry` under `kind="memory"` via a now-real `register_memory_provider`.
2. Do NOT rip out `file_memory`. Add an OPTIONAL bridge: if a memory provider is available (Honcho key set), the `memory` tool's recall can additionally consult it; writes still go to file-memory. Gate the whole bridge behind `JARVIS_MEMORY_PROVIDER` env (default off → zero behavior change).
3. If the bridge proves the abstraction works, a follow-up (separate plan, surfaced to user) can promote a provider to primary. **Do not restructure file_memory in this task** — that's the "if it doesn't work" escalation, and it needs its own design pass + user sign-off.

**Files:**
- Modify: `src/voice-agent/tools/plugin_system.py` (`register_memory_provider` → `_register_provider("memory", provider)`)
- Create: `src/voice-agent/tools/memory_providers.py` (JARVIS-native `MemoryProvider` base + optional recall bridge gated on `JARVIS_MEMORY_PROVIDER`)
- Create: `src/voice-agent/plugins/memory/honcho/{plugin.yaml,__init__.py}`
- Test: `src/voice-agent/tests/test_memory_providers.py`

- [ ] **Step 1: Read** `hermes/agent/memory_provider.py` (the ABC) + `hermes/plugins/memory/honcho/` to learn the interface (likely `is_available`, `search(query)`, `add(...)`, `profile(...)`).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_memory_providers.py
import sys, importlib.util
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_memory_provider_hook_wired():
    from tools import _provider_registry as pr
    from tools.plugin_system import PluginContext, PluginManager, PluginManifest
    pr.reset_providers("memory")
    class M:
        name = "honcho"
        def is_available(self): return True
    PluginContext(PluginManifest(name="t"), PluginManager()).register_memory_provider(M())
    assert pr.get_provider("memory", "honcho") is not None
    pr.reset_providers("memory")


def test_honcho_inert_without_key(monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    spec = importlib.util.spec_from_file_location(
        "_t_honcho", Path(__file__).parent.parent / "plugins/memory/honcho/__init__.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.HonchoMemoryProvider().is_available() is False


def test_memory_bridge_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from tools.memory_providers import memory_bridge_enabled
    assert memory_bridge_enabled() is False
```

- [ ] **Step 3: Run to verify fails** — FAIL.

- [ ] **Step 4: Implement** `tools/memory_providers.py` (base + `memory_bridge_enabled()` reading `JARVIS_MEMORY_PROVIDER`), wire `register_memory_provider`, port `HonchoMemoryProvider` (gate on `HONCHO_API_KEY`). Keep the bridge dormant by default.

- [ ] **Step 5: Tests + discovery + import jarvis_agent** — `pytest tests/test_memory_providers.py tests/test_no_duplicate_tools.py -q && .venv/bin/python -c "import jarvis_agent"` → PASS.

- [ ] **Step 6: Commit** — `git add tools/plugin_system.py tools/memory_providers.py plugins/memory/ tests/test_memory_providers.py && git commit -m "feat(plugins): port memory provider abstraction + Honcho backend (bridge off by default)"`

---

## Task 7: Present-but-honest 1:1 mirror — the 9 inert subsystem/dashboard plugins

Copy the remaining Hermes plugins so `plugins/` mirrors Hermes 1:1, JARVIS-native, importing cleanly but inert in the voice substrate (their contribution types — context-engine, 40 model-provider profiles, chat-platform adapters, Langfuse hooks, session-cleanup hooks, operator CLI, dashboards — have no consumer here). Each `register(ctx)` must run without raising. NO false "duplicate of X" claims — the docstring states honestly *why* it's inert (no voice consumer for this contribution type).

**Plugins:** `context_engine`, `model-providers`, `platforms`, `observability`, `disk-cleanup`, `teams_pipeline`, `example-dashboard`, `achievements` (renamed from `hermes-achievements`), `kanban`.

**Files:**
- Create: `src/voice-agent/plugins/<name>/...` for each (mirror Hermes layout; flat plugins get one `plugin.yaml`+`__init__.py`; category plugins like `platforms/<adapter>`, `model-providers/<provider>` get the 2-level layout with a leaf `plugin.yaml`+`__init__.py` per backend).
- For `example-dashboard`/`achievements`/`kanban` which have no `plugin.yaml` in Hermes: add a minimal `plugin.yaml` (`kind: dashboard`) + an `__init__.py` with an inert `register(ctx)` so discovery sees them (otherwise they're invisible dirs — acceptable, but the user wants them present).
- Test: extend `src/voice-agent/tests/test_no_duplicate_tools.py` is enough; add `tests/test_all_plugins_load.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_all_plugins_load.py
"""Every bundled plugin must load without error and register no duplicate tools."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_all_bundled_plugins_load_without_error():
    from tools.plugin_system import discover_plugins
    m = discover_plugins(force=True)
    broken = [(p["key"], p["error"]) for p in m.list_plugins() if not p["enabled"]]
    assert not broken, f"plugins failed to load: {broken}"


def test_expected_plugin_set_present():
    from tools.plugin_system import discover_plugins
    keys = {p["key"] for p in discover_plugins(force=True).list_plugins()}
    # capability-adders + functional + mirror — top-level keys / category prefixes
    for expected in ["example", "google_meet", "spotify", "web/tavily",
                     "video_gen/fal", "image_gen/codex", "browser/browserbase",
                     "memory/honcho", "context_engine", "platforms/irc",
                     "model-providers/anthropic", "observability/langfuse",
                     "disk-cleanup", "teams_pipeline", "achievements", "kanban",
                     "example-dashboard"]:
        assert expected in keys, f"missing plugin: {expected} (have {sorted(keys)})"
```

- [ ] **Step 2: Run to verify fails** — FAIL (missing keys).

- [ ] **Step 3: Create the 9 mirror plugins.** For each leaf, an inert register:
```python
"""<name> — present for Hermes parity; inert in the JARVIS voice substrate.

This plugin contributes a <context engine / model-provider profile / chat
platform adapter / telemetry hook / dashboard>, which the voice agent has no
consumer for. It is shipped so the plugin set mirrors upstream and discovery is
exercised; register() intentionally contributes nothing here.
"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)


def register(ctx) -> None:
    logger.debug("plugin %s present but inert (no voice consumer for its contribution type)",
                 getattr(getattr(ctx, "manifest", None), "name", "?"))
```
Scrub every `hermes` token from copied `plugin.yaml` descriptions. For category plugins, port the FULL set of leaf dirs Hermes ships (so the mirror is genuinely 1:1) — each leaf an inert `register`. (These don't register providers, so no `_provider_registry` entries and no dup risk.)

- [ ] **Step 4: Run tests + import** — `pytest tests/test_all_plugins_load.py tests/test_no_duplicate_tools.py -q && .venv/bin/python -c "import jarvis_agent"` → PASS.

- [ ] **Step 5: Commit** — `git add plugins/ tests/test_all_plugins_load.py && git commit -m "feat(plugins): mirror 9 Hermes subsystem/dashboard plugins (present + honestly inert)"`

---

## Task 8: Final verification

- [ ] **Step 1: Full suite** — `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → all green (~1996+ tests).
- [ ] **Step 2: No-dup guard** — `pytest tests/test_no_duplicate_tools.py -q` → PASS.
- [ ] **Step 3: Live tool surface check** — `.venv/bin/python -c "from tools._adapter import load_all_livekit_tools; t=load_all_livekit_tools(); print(len(t), 'tools'); print(sorted(x.info.name for x in t if 'web' in x.info.name or 'image' in x.info.name or 'video' in x.info.name))"` → includes `web_extract`/`web_crawl`; no duplicates.
- [ ] **Step 4: Plugin roster** — `.venv/bin/python -c "from tools.plugin_system import discover_plugins; [print(p['key'], p['enabled']) for p in discover_plugins(force=True).list_plugins()]"` → all 16 plugin families present + enabled.
- [ ] **Step 5: Hermes-token scan** — `! grep -rinE 'hermes' src/voice-agent/plugins/ src/voice-agent/tools/web_providers.py src/voice-agent/tools/memory_providers.py` → no matches (exit 0 from the negation).
- [ ] **Step 6: Restart decision** — check `~/.local/share/jarvis/turn_telemetry.db` latest `ts_utc`; if >60s idle, restart `jarvis-voice-agent.service` and confirm clean boot (worker registered, 0 errors, tool count up). Else ask the user.

---

## Self-review notes (filled during writing)

- **Spec coverage:** functional ports (web/video/image/browser/memory) = Tasks 2–6; present-honest mirror (9 plugins) = Task 7; foundation wiring = Task 1; cleanup = Task 0; verification = Task 8. All scope items covered.
- **Naming:** Task 7 Step 3 + Task 8 Step 5 enforce zero hermes tokens; `hermes-achievements`→`achievements` explicit.
- **Dup safety:** every task re-runs `test_no_duplicate_tools.py`; `ddgs` web backend skipped specifically to avoid colliding with built-in `web_search`; mirror plugins register no tools.
- **Live-system safety:** memory restructure deferred to a future plan with user sign-off (Task 6 approach note); turn loop untouched.
