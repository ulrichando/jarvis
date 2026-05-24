"""FAL video backend port — gating, family routing, payload shaping, discovery.

video_generate already resolves the 'video' provider kind (xAI ships in
tools/video_gen.py); this guards that the FAL backend plugin registers a
second video provider and is inert without FAL_KEY.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_fal():
    spec = importlib.util.spec_from_file_location(
        "_t_fal", Path(__file__).parent.parent / "plugins/video_gen/fal/__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fal_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    prov = _load_fal().FALVideoGenProvider()
    assert prov.name == "fal"
    assert prov.is_available() is False


def test_fal_family_resolution_and_routing(monkeypatch):
    monkeypatch.delenv("FAL_VIDEO_MODEL", raising=False)
    mod = _load_fal()
    assert mod._resolve_family(None)[0] == "pixverse-v6"  # default
    assert mod._resolve_family("veo3.1")[0] == "veo3.1"  # explicit
    assert mod._resolve_family("nonexistent")[0] == "pixverse-v6"  # bad → default


def test_fal_build_payload_drops_unsupported_keys():
    mod = _load_fal()
    fam = mod.FAL_FAMILIES["happy-horse"]  # declares no aspect/res/duration, audio=False
    payload = mod._build_payload(
        fam, prompt="x", image_url=None, duration=8, aspect_ratio="16:9",
        resolution="720p", negative_prompt="bad", audio=True, seed=1,
    )
    assert payload["prompt"] == "x"
    assert payload["seed"] == 1
    for dropped in ("aspect_ratio", "resolution", "duration", "negative_prompt", "generate_audio"):
        assert dropped not in payload


def test_fal_kling_uses_start_image_url():
    mod = _load_fal()
    fam = mod.FAL_FAMILIES["kling-v3-4k"]
    payload = mod._build_payload(
        fam, prompt="x", image_url="http://img", duration=None, aspect_ratio="16:9",
        resolution="720p", negative_prompt=None, audio=None, seed=None,
    )
    assert payload.get("start_image_url") == "http://img"
    assert "image_url" not in payload


def test_video_generate_resolves_video_kind():
    import tools.video_gen as vg

    assert getattr(vg, "PROVIDER_KIND", None) == "video"


def test_fal_plugin_discovers():
    from tools.plugin_system import discover_plugins

    rows = {p["key"]: p for p in discover_plugins(force=True).list_plugins()}
    assert "video_gen/fal" in rows
    assert rows["video_gen/fal"]["enabled"] is True
    assert rows["video_gen/fal"]["error"] is None
