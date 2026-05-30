from pipeline import computer_use_vision as cuv


def setup_function(_):
    cuv.clear()


def test_publish_take_newest_and_ttl():
    cuv.publish_capture(png_b64="AAAA", width=800, height=600, action_label="capture", _now=100.0)
    cuv.publish_capture(png_b64="BBBB", width=10, height=10, action_label="capture", _now=101.0)
    cur = cuv.take_current(_now=101.0)            # newest wins
    assert cur["png_b64"] == "BBBB" and cur["width"] == 10
    assert cuv.take_current(ttl_s=20.0, _now=130.0) is None   # past TTL
    assert cuv.take_current(_now=101.0) is not None            # non-consuming


def test_clear_empties_cache_and_trail():
    cuv.publish_capture(png_b64="AAAA", width=1, height=1, _now=1.0)
    cuv.record_action("left_click @ (10,20)")
    cuv.clear()
    assert cuv.take_current(_now=1.0) is None
    assert cuv.recent_actions_text() == ""


def test_record_action_trail_caps_at_3():
    for lbl in ["a", "b", "c", "d"]:
        cuv.record_action(lbl)
    txt = cuv.recent_actions_text()
    assert "d" in txt and "a" not in txt          # deque maxlen=3 evicts oldest
    assert txt.startswith(" (recent:")


def test_publish_ignores_empty_png():
    cuv.publish_capture(png_b64=None, width=1, height=1, _now=1.0)
    assert cuv.take_current(_now=1.0) is None


def test_is_vision_capable():
    assert cuv.is_vision_capable("claude-sonnet-4-6") is True
    assert cuv.is_vision_capable("claude-haiku-4-5") is True
    assert cuv.is_vision_capable("gpt-4o") is True
    assert cuv.is_vision_capable("gemini-2.5-flash") is True
    assert cuv.is_vision_capable("llama-3.3-70b-versatile") is False
    assert cuv.is_vision_capable("deepseek-v4-flash") is False
    assert cuv.is_vision_capable("") is False and cuv.is_vision_capable(None) is False


def test_is_vision_capable_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_VISION_MODEL_PREFIXES", "llama-,foo-")
    assert cuv.is_vision_capable("llama-3.3-70b-versatile") is True
    assert cuv.is_vision_capable("claude-sonnet-4-6") is False


import base64, io


def _png_b64(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (123, 50, 200)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_downscale_png_shrinks_large():
    from PIL import Image
    out = cuv.downscale_png(_png_b64(2400, 1200), max_px=1280)
    assert out is not None
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert max(img.size) <= 1280 and img.size[0] >= img.size[1]   # aspect preserved


def test_downscale_png_keeps_small():
    from PIL import Image
    out = cuv.downscale_png(_png_b64(400, 300), max_px=1280)
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert img.size == (400, 300)


def test_downscale_png_bad_input_returns_none():
    assert cuv.downscale_png("not-base64-@@@") is None
    assert cuv.downscale_png("") is None


class _FakeDispatch:
    def __init__(self, route):
        self.last_route = route


def test_decide_mode_explicit(monkeypatch):
    for m in ("pixels", "text", "off"):
        monkeypatch.setenv("JARVIS_CU_VISION_MODE", m)
        assert cuv.decide_mode(None) == m


def test_decide_mode_auto_defaults_pixels_without_dispatch(monkeypatch):
    monkeypatch.delenv("JARVIS_CU_VISION_MODE", raising=False)
    assert cuv.decide_mode(None) == "pixels"        # uncertainty → pixels (Claude default)


def test_decide_mode_auto_text_only_route(monkeypatch):
    monkeypatch.delenv("JARVIS_CU_VISION_MODE", raising=False)
    monkeypatch.setenv("JARVIS_TASK_DESKTOP_MODEL", "llama-3.3-70b-versatile")
    assert cuv.decide_mode(_FakeDispatch("TASK_DESKTOP")) == "text"


def test_decide_mode_auto_vision_route(monkeypatch):
    monkeypatch.delenv("JARVIS_CU_VISION_MODE", raising=False)
    monkeypatch.delenv("JARVIS_TASK_DESKTOP_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_TASK_MODEL", raising=False)
    assert cuv.decide_mode(_FakeDispatch("TASK_DESKTOP")) == "pixels"   # default claude-sonnet


def test_build_injection_pixels():
    from livekit.agents.llm import ImageContent
    cap = {"png_b64": _png_b64(100, 80), "action_label": "capture"}
    res = cuv.build_injection(cap=cap, mode="pixels")
    assert res is not None
    role, content = res
    assert role == "user"
    assert any(isinstance(c, ImageContent) for c in content)
    assert any(isinstance(c, str) and "screen after" in c for c in content)


def test_build_injection_text():
    cap = {"png_b64": "x", "action_label": "capture"}
    res = cuv.build_injection(cap=cap, mode="text", desc="A settings window is open.")
    role, content = res
    assert role == "user" and "settings window" in content[0]


def test_build_injection_none_cases():
    assert cuv.build_injection(cap=None, mode="pixels") is None
    assert cuv.build_injection(cap={"png_b64": "x"}, mode="off") is None
    assert cuv.build_injection(cap={"png_b64": "x", "action_label": "c"}, mode="text", desc=None) is None


def test_resolve_route_primary_model(monkeypatch):
    from providers.llm import resolve_route_primary_model
    monkeypatch.delenv("JARVIS_TASK_DESKTOP_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_TASK_MODEL", raising=False)
    assert cuv.is_vision_capable(resolve_route_primary_model("TASK_DESKTOP")) is True   # claude default
    monkeypatch.setenv("JARVIS_TASK_DESKTOP_MODEL", "llama-3.3-70b-versatile")
    assert resolve_route_primary_model("TASK_DESKTOP") == "llama-3.3-70b-versatile"     # override wins
    assert resolve_route_primary_model("NOT_A_ROUTE") == ""                              # unknown → ""


def test_capture_response_publishes_frame():
    """tools.computer_use._capture_response should publish the frame to the cache."""
    import tools.computer_use as cu
    from tools.computer_use_backend import CaptureResult
    cuv.clear()
    cap = CaptureResult(mode="som", width=1920, height=1080, png_b64="ZZZZ")
    cu._capture_response(cap)                       # side effect: publish
    cur = cuv.take_current()
    assert cur is not None and cur["png_b64"] == "ZZZZ" and cur["width"] == 1920
