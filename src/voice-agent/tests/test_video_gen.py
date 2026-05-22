"""Tests for the ported video_generate tool + xAI video provider.

Proves:
  (a) ``video_generate`` self-registers in registry.all_entries() after import,
      and the xAI provider lands in the "video" provider-registry kind,
  (b) it appears in load_all_livekit_tools() ONLY when XAI_API_KEY is present
      (gated inert otherwise); check_fn is False with no key,
  (c) with the provider's HTTP submit/poll/download MOCKED, the handler SAVES a
      file under <JARVIS_HOME>/generated/ and returns the path,
  (d) no key → clean inert / structured auth error,
  (e) prompt is required.

NO real API calls. NO network. The xAI HTTP client is mocked end-to-end.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the voice-agent root is importable.
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))


# Minimal valid "video" payload — bytes saved verbatim by save_bytes_video().
_FAKE_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42fake-mp4-body"


@pytest.fixture(autouse=True)
def _jarvis_home(tmp_path, monkeypatch):
    """Sandbox JARVIS_HOME and strip any real xAI key so a developer's local
    env doesn't change results. Individual tests set the key they need."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    from tools.registry import invalidate_check_fn_cache

    invalidate_check_fn_cache()
    yield tmp_path
    invalidate_check_fn_cache()


# ---------------------------------------------------------------------------
# Mock httpx.AsyncClient for the xAI submit → poll → download sequence
# ---------------------------------------------------------------------------


def _resp(json_body=None, *, content=b"", status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body if json_body is not None else {}
    r.content = content
    r.raise_for_status.return_value = None
    return r


def _fake_async_client(*, poll_status="done", video_url="https://cdn.x.ai/v.mp4", download_ok=True):
    """Build a context-manager mock whose .post/.get drive the provider flow.

    post() → submit (returns request_id)
    get(<base>/videos/<id>) → poll (returns status + video.url)
    get(<video_url>) → download (returns bytes)
    """
    client = MagicMock()

    async def _post(url, **kw):
        return _resp({"request_id": "req-123"})

    async def _get(url, **kw):
        if url == video_url:
            if not download_ok:
                raise RuntimeError("download failed")
            return _resp(content=_FAKE_VIDEO_BYTES)
        # poll endpoint
        return _resp({"status": poll_status, "video": {"url": video_url, "duration": 8}, "model": "grok-imagine-video"})

    client.post = AsyncMock(side_effect=_post)
    client.get = AsyncMock(side_effect=_get)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _patched_httpx(ctx):
    fake_httpx = MagicMock()
    fake_httpx.AsyncClient.return_value = ctx
    # The provider catches httpx.HTTPStatusError by type; give it a real-ish class.
    fake_httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    return patch.dict("sys.modules", {"httpx": fake_httpx})


# ---------------------------------------------------------------------------
# (a) self-registration
# ---------------------------------------------------------------------------


class TestSelfRegistration:
    def test_video_generate_registers(self):
        import tools.video_gen  # noqa: F401 — side effect: registers 'video_generate'
        from tools.registry import registry

        entry = registry.get_entry("video_generate")
        assert entry is not None
        assert entry.toolset == "video_gen"
        assert entry.is_async is False
        assert "XAI_API_KEY" in entry.requires_env

    def test_xai_provider_registered_in_video_kind(self):
        import tools.video_gen  # noqa: F401
        from tools import _provider_registry as pr

        names = {p.name for p in pr.list_providers("video")}
        assert "xai" in names


# ---------------------------------------------------------------------------
# (b) gating
# ---------------------------------------------------------------------------


class TestGating:
    def test_check_fn_false_without_key(self):
        import tools.video_gen as vg
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        assert vg.check_video_generation_requirements() is False

    def test_check_fn_true_with_xai_key(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        assert vg.check_video_generation_requirements() is True

    def test_not_in_surface_without_key(self):
        import tools.video_gen  # noqa: F401
        from tools._adapter import load_all_livekit_tools
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        names = [t.info.name for t in load_all_livekit_tools()]
        assert "video_generate" not in names

    def test_in_surface_with_xai_key(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen  # noqa: F401
        from tools._adapter import load_all_livekit_tools
        from livekit.agents.llm import is_raw_function_tool
        from tools.registry import invalidate_check_fn_cache

        invalidate_check_fn_cache()
        tools = load_all_livekit_tools()
        matched = [t for t in tools if t.info.name == "video_generate"]
        assert len(matched) == 1
        assert is_raw_function_tool(matched[0])


# ---------------------------------------------------------------------------
# (c) handler saves a file + returns the path (MOCKED HTTP, no network)
# ---------------------------------------------------------------------------


class TestHandlerSavesFile:
    def test_text_to_video_saves_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg

        ctx, client = _fake_async_client()
        with _patched_httpx(ctx):
            raw = vg._handle_video_generate({"prompt": "a cat surfing", "aspect_ratio": "16:9"})
        result = json.loads(raw)

        assert result["result"].startswith("Generated → ")
        saved = Path(result["path"])
        assert saved.exists()
        assert saved.read_bytes() == _FAKE_VIDEO_BYTES
        assert saved.parent == tmp_path / "generated"
        assert result["provider"] == "xai"
        assert result["modality"] == "text"

        # Submit payload shape: text-to-video has no image key.
        submit_kwargs = client.post.call_args.kwargs
        assert submit_kwargs["json"]["prompt"] == "a cat surfing"
        assert "image" not in submit_kwargs["json"]

    def test_image_to_video_sets_modality_and_image(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg

        ctx, client = _fake_async_client()
        with _patched_httpx(ctx):
            raw = vg._handle_video_generate(
                {"prompt": "animate this", "image_url": "https://img/x.png"}
            )
        result = json.loads(raw)

        assert result["modality"] == "image"
        submit_kwargs = client.post.call_args.kwargs
        assert submit_kwargs["json"]["image"] == {"url": "https://img/x.png"}

    def test_download_failure_falls_back_to_url(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg

        ctx, _client = _fake_async_client(download_ok=False)
        with _patched_httpx(ctx):
            raw = vg._handle_video_generate({"prompt": "x"})
        result = json.loads(raw)

        # Still a success; path is the remote URL since the local save failed.
        assert result["result"].startswith("Generated → ")
        assert result["path"] == "https://cdn.x.ai/v.mp4"

    def test_provider_failure_status_returns_error(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg

        ctx, _client = _fake_async_client(poll_status="failed")
        with _patched_httpx(ctx):
            raw = vg._handle_video_generate({"prompt": "x"})
        result = json.loads(raw)
        assert "error" in result


# ---------------------------------------------------------------------------
# (d)+(e) inert / argument validation
# ---------------------------------------------------------------------------


class TestErrors:
    def test_no_key_returns_clean_error(self):
        import tools.video_gen as vg

        # No XAI_API_KEY → provider unavailable → handler returns a tool_error.
        raw = vg._handle_video_generate({"prompt": "x"})
        result = json.loads(raw)
        assert "error" in result
        assert "XAI_API_KEY" in result["error"]

    def test_missing_prompt_rejected(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg

        raw = vg._handle_video_generate({"prompt": "   "})
        result = json.loads(raw)
        assert "error" in result
        assert "prompt is required" in result["error"]


# ---------------------------------------------------------------------------
# Provider unit: clamps + reference-image rules (no HTTP)
# ---------------------------------------------------------------------------


class TestProviderUnits:
    def test_duration_clamp(self):
        import tools.video_gen as vg

        assert vg._clamp_duration(99, has_reference_images=False) == 15
        assert vg._clamp_duration(0, has_reference_images=False) == 1
        assert vg._clamp_duration(12, has_reference_images=True) == 10  # refs cap at 10
        assert vg._clamp_duration(None, has_reference_images=False) == vg._XAI_DEFAULT_DURATION

    def test_too_many_reference_images_rejected(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg

        provider = vg.XAIVideoGenProvider()
        result = provider.generate(
            prompt="x",
            reference_image_urls=[f"https://img/{i}.png" for i in range(8)],
        )
        assert result["success"] is False
        assert result["error_type"] == "too_many_references"

    def test_image_url_and_refs_conflict(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "test-key")
        import tools.video_gen as vg

        provider = vg.XAIVideoGenProvider()
        result = provider.generate(
            prompt="x",
            image_url="https://img/a.png",
            reference_image_urls=["https://img/b.png"],
        )
        assert result["success"] is False
        assert result["error_type"] == "conflicting_inputs"
