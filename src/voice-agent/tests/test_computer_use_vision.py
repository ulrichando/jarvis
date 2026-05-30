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
