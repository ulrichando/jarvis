"""Tests for tools/computer_backend.py — screenshot capture + coordinate
scaling. Backend ops (xdotool) tested in test_computer_backend_input.py."""
import asyncio
import io

import pytest


def test_scale_for_model_picks_xga_for_4_3_source():
    """A 1600x1200 (4:3 aspect) source scales to 1024x768 (XGA)."""
    from tools.computer_backend import scale_for_model
    # We need a valid PNG of size 1600x1200. Use a 1x1 PNG as a stub and
    # mock the actual PIL call via monkeypatch in real tests; for now
    # we test the picker logic via the helper.
    from tools.computer_backend import _pick_scaling_target
    target = _pick_scaling_target(1600, 1200)
    assert target == (1024, 768)


def test_scale_for_model_picks_wxga_for_16_10_source():
    """A 1920x1200 (16:10) source scales to 1280x800 (WXGA)."""
    from tools.computer_backend import _pick_scaling_target
    target = _pick_scaling_target(1920, 1200)
    assert target == (1280, 800)


def test_scale_for_model_picks_fwxga_for_16_9_source():
    """A 1920x1080 (16:9) source scales to 1366x768 (FWXGA)."""
    from tools.computer_backend import _pick_scaling_target
    target = _pick_scaling_target(1920, 1080)
    assert target == (1366, 768)


def test_scale_for_model_returns_factors():
    """scale_for_model returns (png_bytes, scale_x, scale_y) where the
    factors map model coords back to native screen coords."""
    from tools.computer_backend import scale_for_model
    # Create a small synthetic PNG via PIL (already a transitive dep
    # of mss). The native size determines the scale factors.
    from PIL import Image
    img = Image.new("RGB", (1920, 1080), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    scaled_bytes, sx, sy = scale_for_model(buf.getvalue())
    # 1920 -> 1366 means scale_x = 1920/1366 (model emits in scaled
    # space, we multiply to get back to native).
    assert abs(sx - 1920 / 1366) < 1e-3
    assert abs(sy - 1080 / 768) < 1e-3
    # And the scaled PNG is decodable.
    Image.open(io.BytesIO(scaled_bytes)).verify()


@pytest.mark.asyncio
async def test_take_screenshot_returns_png_bytes(monkeypatch):
    """take_screenshot returns PNG bytes via mss when available."""
    from tools import computer_backend
    # Mock mss to return a fake raw frame (RGB pixels).
    class FakeMss:
        def __init__(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        @property
        def monitors(self):
            return [{"width": 100, "height": 100, "left": 0, "top": 0}]
        def grab(self, mon):
            class Frame:
                size = type("Size", (), {"width": 100, "height": 100})()
                bgra = b"\x80" * (100 * 100 * 4)  # gray BGRA
            return Frame()
    monkeypatch.setattr(computer_backend, "_mss_module", FakeMss)
    monkeypatch.setattr(computer_backend, "_mss_available", True)
    png = await computer_backend.take_screenshot()
    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")
