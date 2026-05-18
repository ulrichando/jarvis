"""Tests for tools/computer_backend.py — screenshot capture + coordinate
scaling + xdotool input ops (click/type/key/scroll/drag/mouse_move).

All I/O boundaries (mss, scrot, xdotool) are monkey-patched so the
tests run without a display."""
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


@pytest.mark.asyncio
async def test_click_invokes_xdotool_with_right_argv(monkeypatch):
    """A left-click at (340, 220) should run `xdotool mousemove ... click 1`."""
    from tools import computer_backend
    captured = {}

    async def fake_exec(*argv, **kw):
        captured["argv"] = argv
        class Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
            async def wait(self): return 0
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await computer_backend.click(340, 220)
    argv = captured["argv"]
    assert "xdotool" in argv[0]
    assert "mousemove" in argv
    assert "--sync" in argv
    assert "340" in argv and "220" in argv
    assert "click" in argv
    assert "1" in argv          # left button


@pytest.mark.asyncio
async def test_click_with_modifier_holds_key(monkeypatch):
    """A shift+click adds keydown/keyup around the click."""
    from tools import computer_backend
    seen_argvs = []

    async def fake_exec(*argv, **kw):
        seen_argvs.append(argv)
        class Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
            async def wait(self): return 0
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await computer_backend.click(100, 100, modifiers=["shift"])
    # Should have called keydown shift, click, keyup shift (3 invocations
    # OR a single combined xdotool call with --clearmodifiers).
    joined = " ".join(" ".join(a) for a in seen_argvs)
    assert "shift" in joined.lower()


@pytest.mark.asyncio
async def test_type_text_invokes_xdotool_type(monkeypatch):
    from tools import computer_backend
    captured = {}

    async def fake_exec(*argv, **kw):
        captured["argv"] = argv
        class Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
            async def wait(self): return 0
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await computer_backend.type_text("hello world")
    argv = captured["argv"]
    assert "type" in argv
    assert "hello world" in argv


@pytest.mark.asyncio
async def test_key_combo_invokes_xdotool_key(monkeypatch):
    from tools import computer_backend
    captured = {}

    async def fake_exec(*argv, **kw):
        captured["argv"] = argv
        class Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
            async def wait(self): return 0
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await computer_backend.key_combo("ctrl+s")
    argv = captured["argv"]
    assert "key" in argv
    assert "ctrl+s" in argv


@pytest.mark.asyncio
async def test_xdotool_nonzero_raises_backenderror(monkeypatch):
    from tools import computer_backend

    async def fake_exec(*argv, **kw):
        class Proc:
            returncode = 1
            async def communicate(self): return (b"", b"some error")
            async def wait(self): return 1
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(computer_backend.BackendError):
        await computer_backend.click(0, 0)
