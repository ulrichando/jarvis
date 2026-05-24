"""Tests for the ported image_generate tool + provider-registry shim.

Proves:
  (a) ``image_generate`` self-registers in registry.all_entries() after import,
  (b) it appears in load_all_livekit_tools() ONLY when a provider key is present
      (gated inert otherwise),
  (c) check_fn is False with no key (tool filtered out, inert),
  (d) with the provider + network MOCKED, the handler SAVES a file and returns
      the path,
  (e) no key → clean inert / structured error.

NO real API calls. NO network. All backends are mocked.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Ensure the voice-agent root is importable.
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))


# 1×1 transparent PNG — valid bytes for save_b64_image().
_PNG_HEX = (
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44"
    "ae426082"
)


def _b64_png() -> str:
    return base64.b64encode(bytes.fromhex(_PNG_HEX)).decode()


@pytest.fixture(autouse=True)
def _jarvis_home(tmp_path, monkeypatch):
    """Point JARVIS_HOME at a tmp dir so saved images land in the sandbox.

    Also strips any real provider keys from the environment so a developer who
    has OPENAI_API_KEY / XAI_API_KEY set locally gets deterministic results;
    individual tests set the key(s) they need.
    """
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("XAI_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("XAI_IMAGE_RESOLUTION", raising=False)
    # check_fn results are TTL-cached on the env probe; clear so per-test
    # key flips take effect immediately.
    from tools.registry import invalidate_check_fn_cache
    invalidate_check_fn_cache()
    yield tmp_path
    invalidate_check_fn_cache()


def _fake_openai_response(*, b64=None, url=None, revised_prompt=None):
    item = SimpleNamespace(b64_json=b64, url=url, revised_prompt=revised_prompt)
    return SimpleNamespace(data=[item])


def _patched_openai(fake_client: MagicMock):
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client
    return patch.dict("sys.modules", {"openai": fake_openai})


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# (a) self-registration
# ---------------------------------------------------------------------------

class TestSelfRegistration:
    def test_image_generate_registers(self):
        import tools.image_gen  # noqa: F401 — side effect: registers 'image_generate'
        from tools.registry import registry

        entry = registry.get_entry("image_generate")
        assert entry is not None
        assert entry.toolset == "image_gen"
        assert entry.is_async is False
        assert "OPENAI_API_KEY" in entry.requires_env
        assert "XAI_API_KEY" in entry.requires_env

    def test_both_providers_registered(self):
        import tools.image_gen  # noqa: F401
        from tools import _provider_registry as pr

        names = {p.name for p in pr.list_providers("image")}
        assert names == {"openai", "xai"}


# ---------------------------------------------------------------------------
# (b)+(c) gating: surfaced only when a key is present; inert otherwise
# ---------------------------------------------------------------------------

class TestGating:
    def test_check_fn_false_without_key(self):
        import tools.image_gen as ig
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        assert ig.check_image_generation_requirements() is False

    def test_check_fn_true_with_openai_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen as ig
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        assert ig.check_image_generation_requirements() is True

    def test_check_fn_true_with_xai_key(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.image_gen as ig
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        assert ig.check_image_generation_requirements() is True

    def test_not_in_surface_without_key(self):
        import tools.image_gen  # noqa: F401
        from tools._adapter import load_all_livekit_tools
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        names = [t.info.name for t in load_all_livekit_tools()]
        assert "image_generate" not in names

    def test_in_surface_with_openai_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen  # noqa: F401
        from tools._adapter import load_all_livekit_tools
        from livekit.agents.llm import is_raw_function_tool
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        tools = load_all_livekit_tools()
        matched = [t for t in tools if t.info.name == "image_generate"]
        assert len(matched) == 1
        assert is_raw_function_tool(matched[0])


# ---------------------------------------------------------------------------
# Provider-registry shim — generic behavior
# ---------------------------------------------------------------------------

class TestProviderRegistry:
    def test_register_and_get_by_name(self):
        from tools import _provider_registry as pr

        pr.reset_providers("unit_test_kind")
        sentinel = SimpleNamespace(name="alpha", is_available=lambda: True)
        pr.register_provider("unit_test_kind", "alpha", sentinel)
        assert pr.get_provider("unit_test_kind", "alpha") is sentinel
        pr.reset_providers("unit_test_kind")

    def test_get_provider_auto_picks_available(self):
        from tools import _provider_registry as pr

        pr.reset_providers("unit_test_kind")
        down = SimpleNamespace(name="down", is_available=lambda: False)
        up = SimpleNamespace(name="up", is_available=lambda: True)
        pr.register_provider("unit_test_kind", "down", down)
        pr.register_provider("unit_test_kind", "up", up)
        # name omitted → first AVAILABLE provider, regardless of sort order.
        assert pr.get_provider("unit_test_kind") is up
        assert pr.has_available_provider("unit_test_kind") is True
        pr.reset_providers("unit_test_kind")

    def test_get_provider_by_name_returns_even_if_unavailable(self):
        from tools import _provider_registry as pr

        pr.reset_providers("unit_test_kind")
        down = SimpleNamespace(name="down", is_available=lambda: False)
        pr.register_provider("unit_test_kind", "down", down)
        # Explicit name wins regardless of availability (precise error upstream).
        assert pr.get_provider("unit_test_kind", "down") is down
        # ...but auto-resolution skips it.
        assert pr.get_provider("unit_test_kind") is None
        assert pr.has_available_provider("unit_test_kind") is False
        pr.reset_providers("unit_test_kind")

    def test_buggy_is_available_does_not_break_resolution(self):
        from tools import _provider_registry as pr

        def _boom():
            raise RuntimeError("provider probe blew up")

        pr.reset_providers("unit_test_kind")
        bad = SimpleNamespace(name="bad", is_available=_boom)
        good = SimpleNamespace(name="good", is_available=lambda: True)
        pr.register_provider("unit_test_kind", "bad", bad)
        pr.register_provider("unit_test_kind", "good", good)
        assert pr.get_provider("unit_test_kind") is good
        pr.reset_providers("unit_test_kind")

    def test_empty_kind_or_name_rejected(self):
        from tools import _provider_registry as pr

        with pytest.raises(ValueError):
            pr.register_provider("", "x", object())
        with pytest.raises(ValueError):
            pr.register_provider("k", "", object())


# ---------------------------------------------------------------------------
# (d) handler saves a file + returns the path (MOCKED provider, no network)
# ---------------------------------------------------------------------------

class TestHandlerSavesFile:
    def test_openai_b64_saves_file_and_returns_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen as ig

        png_bytes = bytes.fromhex(_PNG_HEX)
        fake_client = MagicMock()
        fake_client.images.generate.return_value = _fake_openai_response(b64=_b64_png())

        with _patched_openai(fake_client):
            raw = ig._handle_image_generate({"prompt": "a red cube", "aspect_ratio": "landscape"})
        result = json.loads(raw)

        # Voice-friendly + machine-readable path.
        assert result["result"].startswith("Generated → ")
        saved = Path(result["path"])
        assert saved.exists()
        assert saved.read_bytes() == png_bytes
        # Landed under <JARVIS_HOME>/generated/.
        assert saved.parent == tmp_path / "generated"
        assert result["provider"] == "openai"
        assert result["aspect_ratio"] == "landscape"

        # Correct API call shape: single underlying model, no response_format.
        call_kwargs = fake_client.images.generate.call_args.kwargs
        assert call_kwargs["model"] == "gpt-image-2"
        assert call_kwargs["quality"] == "medium"
        assert call_kwargs["size"] == "1536x1024"
        assert "response_format" not in call_kwargs

    def test_xai_b64_saves_file_and_returns_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        # OpenAI absent → registry auto-resolves to xAI.
        import tools.image_gen as ig

        png_bytes = bytes.fromhex(_PNG_HEX)
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"data": [{"b64_json": _b64_png()}]}

        with patch("requests.post", return_value=fake_resp) as mock_post:
            raw = ig._handle_image_generate({"prompt": "a blue sphere", "aspect_ratio": "square"})
        result = json.loads(raw)

        assert result["result"].startswith("Generated → ")
        saved = Path(result["path"])
        assert saved.exists()
        assert saved.read_bytes() == png_bytes
        assert saved.parent == tmp_path / "generated"
        assert result["provider"] == "xai"

        # xAI endpoint + payload shape.
        called_url = mock_post.call_args.args[0]
        assert called_url.endswith("/images/generations")
        sent = mock_post.call_args.kwargs["json"]
        assert sent["model"] == "grok-imagine-image"
        assert sent["aspect_ratio"] == "1:1"  # square

    def test_openai_aspect_ratio_mapping(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen as ig

        for aspect, expected_size in [
            ("landscape", "1536x1024"),
            ("square", "1024x1024"),
            ("portrait", "1024x1536"),
        ]:
            fake_client = MagicMock()
            fake_client.images.generate.return_value = _fake_openai_response(b64=_b64_png())
            with _patched_openai(fake_client):
                ig._handle_image_generate({"prompt": "x", "aspect_ratio": aspect})
            assert fake_client.images.generate.call_args.kwargs["size"] == expected_size

    def test_async_invocation_through_adapter(self, monkeypatch):
        """End-to-end through the LiveKit adapter wrapper (raw_arguments binding)."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen  # noqa: F401
        from tools._adapter import load_all_livekit_tools
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        fake_client = MagicMock()
        fake_client.images.generate.return_value = _fake_openai_response(b64=_b64_png())

        with _patched_openai(fake_client):
            tools = load_all_livekit_tools()
            tool = next(t for t in tools if t.info.name == "image_generate")
            raw = _run(tool(raw_arguments={"prompt": "a cat", "aspect_ratio": "square"}))
        result = json.loads(raw)
        assert result["result"].startswith("Generated → ")
        assert Path(result["path"]).exists()


# ---------------------------------------------------------------------------
# (e) clean inert / error paths
# ---------------------------------------------------------------------------

class TestErrorPaths:
    def test_no_key_returns_clean_error(self):
        import tools.image_gen as ig
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        raw = ig._handle_image_generate({"prompt": "anything"})
        result = json.loads(raw)
        assert "error" in result
        assert "OPENAI_API_KEY" in result["error"] or "XAI_API_KEY" in result["error"]
        # No file created, no exception raised.

    def test_empty_prompt_rejected(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen as ig

        raw = ig._handle_image_generate({"prompt": "   "})
        result = json.loads(raw)
        assert "error" in result
        assert "prompt is required" in result["error"].lower()

    def test_provider_api_error_surfaced(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen as ig

        fake_client = MagicMock()
        fake_client.images.generate.side_effect = RuntimeError("boom")
        with _patched_openai(fake_client):
            raw = ig._handle_image_generate({"prompt": "a cat"})
        result = json.loads(raw)
        assert "error" in result
        assert "boom" in result["error"]
        assert result.get("error_type") == "api_error"

    def test_empty_response_data_surfaced(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        import tools.image_gen as ig

        fake_client = MagicMock()
        fake_client.images.generate.return_value = SimpleNamespace(data=[])
        with _patched_openai(fake_client):
            raw = ig._handle_image_generate({"prompt": "a cat"})
        result = json.loads(raw)
        assert "error" in result
        assert result.get("error_type") == "empty_response"


# ---------------------------------------------------------------------------
# No-duplicate-name guard (image_generate must not clash with anything)
# ---------------------------------------------------------------------------

class TestNoDuplicate:
    def test_no_duplicate_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        from collections import Counter
        from tools._adapter import load_all_livekit_tools
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        names = [t.info.name for t in load_all_livekit_tools()]
        dups = [n for n, c in Counter(names).items() if c > 1]
        assert not dups, f"duplicate tool names: {dups}"
        assert names.count("image_generate") == 1
