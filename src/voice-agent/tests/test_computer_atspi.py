"""Tests for tools/computer_atspi.py — AT-SPI widget enumeration with
graceful fallback when AT-SPI is unavailable."""
import pytest


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the computer_atspi module cache before each test."""
    from tools import computer_atspi
    computer_atspi._CACHE_KEY = None
    computer_atspi._CACHE_VAL = []
    computer_atspi._CACHE_TS = 0.0
    yield
    # Reset after test too
    computer_atspi._CACHE_KEY = None
    computer_atspi._CACHE_VAL = []
    computer_atspi._CACHE_TS = 0.0


def test_enumerate_widgets_empty_when_dbus_unavailable(monkeypatch):
    """When pyatspi fails (D-Bus session missing, e.g. in CI), the
    function returns [] silently rather than raising."""
    from tools import computer_atspi

    def fake_get_desktop(*a, **kw):
        raise RuntimeError("no D-Bus")

    monkeypatch.setattr(computer_atspi, "_get_desktop", fake_get_desktop)
    widgets = computer_atspi.enumerate_widgets()
    assert widgets == []


def test_enumerate_widgets_returns_dataclass(monkeypatch):
    """When pyatspi works, returns a list of Widget dataclass instances
    with bounds/role/text/enabled/active populated."""
    from tools import computer_atspi
    from tools.computer_atspi import Widget

    class FakeAcc:
        def __init__(self, role, name, x, y, w, h, enabled=True, active=False):
            self._role = role
            self._name = name
            self._bounds = (x, y, w, h)
            self._enabled = enabled
            self._active = active
        def getRoleName(self): return self._role
        @property
        def name(self): return self._name
        def queryComponent(self):
            class C:
                def getExtents(_self, _coord_type):
                    x, y, w, h = self._bounds
                    class E:
                        pass
                    e = E()
                    e.x, e.y, e.width, e.height = x, y, w, h
                    return e
            return C()
        def getState(self):
            class S:
                contains = lambda _self, s: (s == "enabled" and self._enabled) or (s == "active" and self._active)
            return S()

    class FakeApp:
        """Fake application with one child (a window frame)."""
        def __init__(self, window_frame):
            self._window = window_frame
        @property
        def childCount(self):
            return 1
        def getChildAtIndex(self, i):
            if i == 0:
                return self._window
            raise IndexError

    fake_window = FakeAcc("frame", "TestWin", 0, 0, 1920, 1080, active=True)
    fake_button = FakeAcc("push_button", "Save", 100, 200, 80, 30)
    fake_app = FakeApp(fake_window)

    # We swap _enumerate_descendants to return our synthetic list
    monkeypatch.setattr(
        computer_atspi, "_enumerate_descendants",
        lambda _root: [fake_button]
    )
    monkeypatch.setattr(computer_atspi, "_get_desktop", lambda: [fake_app])

    widgets = computer_atspi.enumerate_widgets()
    assert len(widgets) == 1
    assert isinstance(widgets[0], Widget)
    assert widgets[0].role == "push_button"
    assert widgets[0].text == "Save"
    assert widgets[0].bounds == (100, 200, 80, 30)


def test_enumerate_widgets_cache(monkeypatch):
    """Two calls within 100ms hit the cache; third call after cache
    expiry re-enumerates."""
    import time
    from tools import computer_atspi

    calls = {"n": 0}
    def fake_enum(_root):
        calls["n"] += 1
        return []

    class FakeWindow:
        @property
        def childCount(self):
            return 0
        def getChildAtIndex(self, i):
            raise IndexError

    class FakeApp:
        def __init__(self):
            self._window = FakeWindow()
        @property
        def childCount(self):
            return 1
        def getChildAtIndex(self, i):
            if i == 0:
                return self._window
            raise IndexError

    monkeypatch.setattr(computer_atspi, "_enumerate_descendants", fake_enum)
    monkeypatch.setattr(computer_atspi, "_get_desktop", lambda: [FakeApp()])

    computer_atspi.enumerate_widgets()
    computer_atspi.enumerate_widgets()  # within 100ms
    assert calls["n"] == 1, "second call should hit cache"
    # Force cache expiry by manipulating module clock
    computer_atspi._CACHE_TS = 0.0
    computer_atspi.enumerate_widgets()
    assert calls["n"] == 2
