"""Tests for tools.browser_control — keystroke control of the live browser.

No real X11 / xdotool is touched: every test mocks the four
``tools.desktop_control`` helpers (``xdotool_call`` / ``activate_window`` /
``send_keys`` / ``type_text``) the tool calls, then asserts the exact
keystrokes + window-resolution + read assembly + the ``check_fn`` gate.

The handler references the helpers as ``desktop_control.<fn>`` at call time, so
patching the attribute on the ``desktop_control`` module is seen by the handler.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make ``tools`` importable from this tests dir.
sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.browser_control as bc  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def make_xdotool(search_result="111 222 333", active="222",
                 winname="GitHub - Google Chrome", search_ok=True):
    """Return a fake ``xdotool_call`` dispatching on the first arg."""
    def fake(args):
        head = args[0] if args else ""
        if head == "search":
            return (True, search_result) if search_ok else (False, "xdotool exited 1")
        if head == "getactivewindow":
            return (True, active)
        if head == "getwindowname":
            return (True, winname)
        return (False, "unexpected xdotool call")
    return fake


class Recorder:
    """Records send_keys / type_text calls; mimics desktop_control surface."""

    def __init__(self, send_ret=True):
        self.keys: list[str] = []
        self.text: list[str] = []
        self.send_ret = send_ret

    def send_keys(self, k):
        self.keys.append(k)
        return self.send_ret

    def type_text(self, t):
        self.text.append(t)
        return True


@pytest.fixture
def wired(monkeypatch):
    """Wire a happy-path browser window + recorder; zero the settle delay."""
    rec = Recorder()
    monkeypatch.setattr(bc, "_SETTLE_S", 0.0)
    monkeypatch.setattr(bc.desktop_control, "xdotool_call", make_xdotool())
    monkeypatch.setattr(bc.desktop_control, "activate_window", lambda wid: True)
    monkeypatch.setattr(bc.desktop_control, "send_keys", rec.send_keys)
    monkeypatch.setattr(bc.desktop_control, "type_text", rec.type_text)
    return rec


def _r(out: str) -> dict:
    return json.loads(out)


# ---------------------------------------------------------------------------
# check_fn gate
# ---------------------------------------------------------------------------


def test_check_fn_all_present(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(bc.shutil, "which", lambda n: "/usr/bin/xdotool")
    assert bc._browser_control_available() is True


def test_check_fn_no_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(bc.shutil, "which", lambda n: "/usr/bin/xdotool")
    assert bc._browser_control_available() is False


def test_check_fn_no_xdotool(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(bc.shutil, "which", lambda n: None)
    assert bc._browser_control_available() is False


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------


def test_resolve_prefers_active(monkeypatch):
    monkeypatch.setattr(bc.desktop_control, "xdotool_call",
                        make_xdotool(search_result="111 222 333", active="222"))
    wid, err = bc._resolve_browser_window()
    assert wid == 222 and err == ""


def test_resolve_falls_back_to_last(monkeypatch):
    monkeypatch.setattr(bc.desktop_control, "xdotool_call",
                        make_xdotool(search_result="111 222 333", active="999"))
    wid, err = bc._resolve_browser_window()
    assert wid == 333 and err == ""


def test_resolve_no_windows(monkeypatch):
    monkeypatch.setattr(bc.desktop_control, "xdotool_call", make_xdotool(search_ok=False))
    wid, err = bc._resolve_browser_window()
    assert wid is None and "no visible browser" in err


def test_resolve_xdotool_missing(monkeypatch):
    def fake(args):
        return (False, "xdotool not installed")
    monkeypatch.setattr(bc.desktop_control, "xdotool_call", fake)
    wid, err = bc._resolve_browser_window()
    assert wid is None and "not installed" in err


# ---------------------------------------------------------------------------
# Keystroke actions
# ---------------------------------------------------------------------------


def test_new_tab_blank(wired):
    out = _r(bc.browser_control("new_tab"))
    assert out["ok"] is True
    assert wired.keys == ["ctrl+t"]
    assert wired.text == []


def test_new_tab_with_url(wired):
    out = _r(bc.browser_control("new_tab", url="example.com"))
    assert out["ok"] is True
    assert wired.keys == ["ctrl+t", "Return"]
    assert wired.text == ["example.com"]


def test_open_url(wired):
    out = _r(bc.browser_control("open_url", url="https://x.com"))
    assert out["ok"] is True
    assert wired.keys == ["ctrl+l", "Return"]
    assert wired.text == ["https://x.com"]


def test_open_url_requires_url(wired):
    assert "error" in _r(bc.browser_control("open_url"))


def test_close_tab(wired):
    assert _r(bc.browser_control("close_tab"))["ok"] is True
    assert wired.keys == ["ctrl+w"]


def test_next_and_prev_tab(wired):
    bc.browser_control("next_tab")
    bc.browser_control("prev_tab")
    assert wired.keys == ["ctrl+Next", "ctrl+Prior"]


def test_goto_tab(wired):
    assert _r(bc.browser_control("goto_tab", index=3))["ok"] is True
    assert wired.keys == ["ctrl+3"]


def test_goto_tab_last(wired):
    out = _r(bc.browser_control("goto_tab", index=9))
    assert out["ok"] is True and "last" in out["detail"]
    assert wired.keys == ["ctrl+9"]


def test_goto_tab_invalid(wired):
    assert "error" in _r(bc.browser_control("goto_tab", index=0))
    assert "error" in _r(bc.browser_control("goto_tab", index=12))
    assert "error" in _r(bc.browser_control("goto_tab"))  # missing index


def test_scroll_directions(wired):
    bc.browser_control("scroll", direction="down")
    bc.browser_control("scroll", direction="up")
    bc.browser_control("scroll", direction="top")
    bc.browser_control("scroll", direction="bottom")
    assert wired.keys == ["Next", "Prior", "Home", "End"]


def test_scroll_default_is_down(wired):
    assert _r(bc.browser_control("scroll"))["ok"] is True
    assert wired.keys == ["Next"]


def test_scroll_invalid_direction(wired):
    assert "error" in _r(bc.browser_control("scroll", direction="sideways"))


def test_find(wired):
    out = _r(bc.browser_control("find", query="needle"))
    assert out["ok"] is True
    assert wired.keys == ["ctrl+f"]
    assert wired.text == ["needle"]


def test_find_requires_query(wired):
    assert "error" in _r(bc.browser_control("find"))


def test_unknown_action(wired):
    assert "error" in _r(bc.browser_control("frobnicate"))


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_no_browser_window(monkeypatch):
    monkeypatch.setattr(bc, "_SETTLE_S", 0.0)
    monkeypatch.setattr(bc.desktop_control, "xdotool_call", make_xdotool(search_ok=False))
    out = _r(bc.browser_control("new_tab"))
    assert "error" in out and "no visible browser" in out["error"]


def test_activate_failure(wired, monkeypatch):
    monkeypatch.setattr(bc.desktop_control, "activate_window", lambda wid: False)
    out = _r(bc.browser_control("new_tab"))
    assert "error" in out and "focus" in out["error"]


def test_send_keys_failure_surfaced(wired):
    wired.send_ret = False
    out = _r(bc.browser_control("new_tab"))
    assert "error" in out


# ---------------------------------------------------------------------------
# current_tab (read)
# ---------------------------------------------------------------------------


def test_current_tab_with_url(wired, monkeypatch):
    monkeypatch.setattr(bc, "_read_current_url", lambda: "https://github.com/foo")
    out = _r(bc.browser_control("current_tab"))
    assert out["ok"] is True
    assert out["title"] == "GitHub"           # stripped from "GitHub - Google Chrome"
    assert out["url"] == "https://github.com/foo"


def test_current_tab_no_clipboard(wired, monkeypatch):
    monkeypatch.setattr(bc, "_read_current_url", lambda: None)
    out = _r(bc.browser_control("current_tab"))
    assert out["ok"] is True
    assert "url" not in out
    assert "url_note" in out


def test_title_suffix_stripping(wired, monkeypatch):
    for raw, want in [
        ("Inbox - Mozilla Firefox", "Inbox"),
        ("Docs — Brave", "Docs"),
        ("Plain title with no suffix", "Plain title with no suffix"),
    ]:
        monkeypatch.setattr(bc.desktop_control, "xdotool_call",
                            make_xdotool(winname=raw))
        monkeypatch.setattr(bc, "_read_current_url", lambda: None)
        out = _r(bc.browser_control("current_tab"))
        assert out["title"] == want


# ---------------------------------------------------------------------------
# URL clipboard read helper
# ---------------------------------------------------------------------------


def test_read_current_url_no_tool(monkeypatch):
    monkeypatch.setattr(bc, "_clip_tool", lambda: None)
    assert bc._read_current_url() is None


def test_read_current_url_roundtrip(monkeypatch):
    monkeypatch.setattr(bc, "_SETTLE_S", 0.0)
    monkeypatch.setattr(bc, "_clip_tool", lambda: "/usr/bin/xclip")
    state = {"clip": "OLD-CLIPBOARD"}
    restored: list[str] = []

    monkeypatch.setattr(bc, "_clip_get", lambda _t: state["clip"])

    def fake_set(_t, v):
        restored.append(v)
        state["clip"] = v
    monkeypatch.setattr(bc, "_clip_set", fake_set)

    keys: list[str] = []

    def fake_send(k):
        keys.append(k)
        if k == "ctrl+c":           # copying the omnibox populates the clipboard
            state["clip"] = "https://live.example/page"
        return True
    monkeypatch.setattr(bc.desktop_control, "send_keys", fake_send)

    url = bc._read_current_url()
    assert url == "https://live.example/page"
    assert keys == ["ctrl+l", "ctrl+c", "Escape"]
    assert restored == ["OLD-CLIPBOARD"]      # prior clipboard restored
    assert state["clip"] == "OLD-CLIPBOARD"


# ---------------------------------------------------------------------------
# list_tabs (CDP — reads the live browser's tab list over the debug port)
# ---------------------------------------------------------------------------


def test_list_tabs_ok(monkeypatch):
    monkeypatch.setattr(bc, "_cdp_list_pages", lambda *a, **k: [
        {"type": "page", "title": "YouTube", "url": "https://youtube.com"},
        {"type": "page", "title": "GitHub", "url": "https://github.com"},
    ])
    out = _r(bc.browser_control("list_tabs"))
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["tabs"] == ["YouTube", "GitHub"]
    assert "2 tab" in out["detail"]


def test_list_tabs_port_closed(monkeypatch):
    monkeypatch.setattr(bc, "_cdp_list_pages", lambda *a, **k: None)
    out = _r(bc.browser_control("list_tabs"))
    assert "error" in out
    assert "debug port" in out["error"]


def test_list_tabs_needs_no_window(monkeypatch):
    # list_tabs must NOT require xdotool/window resolution — nothing wired here.
    monkeypatch.setattr(bc, "_cdp_list_pages", lambda *a, **k: [])
    out = _r(bc.browser_control("list_tabs"))
    assert out["ok"] is True and out["count"] == 0


class _FakeResp:
    """Minimal urlopen() context-manager stand-in."""

    def __init__(self, data: bytes):
        self._d = data

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_cdp_list_pages_filters_non_pages(monkeypatch):
    payload = json.dumps([
        {"type": "page", "title": "A", "url": "https://a"},
        {"type": "service_worker", "title": "sw", "url": "https://sw"},
        {"type": "page", "title": "DT", "url": "devtools://devtools/x"},
        {"type": "background_page", "title": "bg", "url": "chrome-extension://y"},
    ]).encode()
    monkeypatch.setattr(bc.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload))
    pages = bc._cdp_list_pages(9222)
    assert [p["title"] for p in pages] == ["A"]   # only the real, non-devtools page


def test_cdp_list_pages_closed_port(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(bc.urllib.request, "urlopen", boom)
    assert bc._cdp_list_pages(9222) is None


# ---------------------------------------------------------------------------
# Registration smoke
# ---------------------------------------------------------------------------


def test_registered():
    from tools.registry import registry
    entry = registry.get_entry("browser_control")
    assert entry is not None
    assert entry.toolset == "browser"
    assert entry.is_async is False
    assert entry.check_fn is bc._browser_control_available
