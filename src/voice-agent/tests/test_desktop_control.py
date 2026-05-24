"""Tests for tools.desktop_control — cross-platform keystroke / window helpers.

The helpers MUST NOT actually drive an X11 server or open a real pywinauto
session in tests; every test mocks either ``subprocess.run`` (for the Linux
backend) or a fake ``pywinauto.keyboard`` / ``pywinauto.findwindows`` /
``pywinauto.application`` module (for the Windows backend).

Coverage shape:
  * Linux path uses xdotool argv exactly as the inline ``type_in_terminal``
    code did pre-refactor (regression guard for the lift-and-shift).
  * Windows path uses the fake pywinauto module so we can assert the
    *translated* key strings (``"super+l"`` → ``"{LWIN down}l{LWIN up}"`` etc.)
    without needing the real package on a Linux dev box.
  * Every helper swallows exceptions into the documented sentinel
    (False / None / (False, _)) — no exception ever bubbles up into the
    voice loop's tool flow.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

# Make ``tools`` importable from this tests dir.
sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.desktop_control as dc  # noqa: E402


# ---------------------------------------------------------------------------
# Fake-pywinauto fixture (Windows path)
# ---------------------------------------------------------------------------


class _FakeKeyboard:
    """Records ``send_keys`` calls; mimics pywinauto.keyboard."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raise_exc: BaseException | None = None

    def send_keys(self, keys, **kwargs):
        self.calls.append({"keys": keys, "kwargs": kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc


class _FakeFindWindows:
    """Records ``find_windows`` calls; mimics pywinauto.findwindows."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result: list[int] = []
        self.raise_exc: BaseException | None = None

    def find_windows(self, **kwargs):
        self.calls.append({"kwargs": kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.result)


class _FakeWindow:
    """Records set_focus / minimize / restore calls; mimics pywinauto top_window()."""

    def __init__(self, app: "_FakeApplication") -> None:
        self._app = app

    def set_focus(self):
        self._app.actions.append("set_focus")
        if self._app.raise_on_action:
            raise self._app.raise_on_action

    def minimize(self):
        self._app.actions.append("minimize")
        if self._app.raise_on_action:
            raise self._app.raise_on_action

    def restore(self):
        self._app.actions.append("restore")
        if self._app.raise_on_action:
            raise self._app.raise_on_action


class _FakeApplication:
    """Records connect()/top_window() calls; mimics pywinauto.application.Application."""

    instances: list["_FakeApplication"] = []

    def __init__(self) -> None:
        self.connected_handle: int | None = None
        self.actions: list[str] = []
        self.raise_on_connect: BaseException | None = None
        self.raise_on_action: BaseException | None = None
        _FakeApplication.instances.append(self)

    def connect(self, **kwargs):
        self.connected_handle = kwargs.get("handle")
        if self.raise_on_connect is not None:
            raise self.raise_on_connect
        return self

    def top_window(self):
        return _FakeWindow(self)


@pytest.fixture
def fake_pywinauto(monkeypatch):
    """Install a fake ``pywinauto`` package + submodules in ``sys.modules``.

    Returns a SimpleNamespace exposing:
      .keyboard      — _FakeKeyboard
      .findwindows   — _FakeFindWindows
      .application_cls — the _FakeApplication class (instances are appended
                         to its .instances list as the code under test
                         constructs them)
    """
    pkg = types.ModuleType("pywinauto")
    kb_mod = types.ModuleType("pywinauto.keyboard")
    fw_mod = types.ModuleType("pywinauto.findwindows")
    app_mod = types.ModuleType("pywinauto.application")

    fake_kb = _FakeKeyboard()
    fake_fw = _FakeFindWindows()
    _FakeApplication.instances = []

    kb_mod.send_keys = fake_kb.send_keys  # type: ignore[attr-defined]
    fw_mod.find_windows = fake_fw.find_windows  # type: ignore[attr-defined]
    app_mod.Application = _FakeApplication  # type: ignore[attr-defined]

    pkg.keyboard = kb_mod  # type: ignore[attr-defined]
    pkg.findwindows = fw_mod  # type: ignore[attr-defined]
    pkg.application = app_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pywinauto", pkg)
    monkeypatch.setitem(sys.modules, "pywinauto.keyboard", kb_mod)
    monkeypatch.setitem(sys.modules, "pywinauto.findwindows", fw_mod)
    monkeypatch.setitem(sys.modules, "pywinauto.application", app_mod)

    return types.SimpleNamespace(
        keyboard=fake_kb,
        findwindows=fake_fw,
        application_cls=_FakeApplication,
    )


@pytest.fixture
def force_linux(monkeypatch):
    monkeypatch.setattr(dc.platform, "system", lambda: "Linux")


@pytest.fixture
def force_windows(monkeypatch):
    monkeypatch.setattr(dc.platform, "system", lambda: "Windows")


# ---------------------------------------------------------------------------
# xdotool → pywinauto key translation table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "xdotool, pywinauto",
    [
        # Bare special keys
        ("Return",          "{ENTER}"),
        ("Enter",           "{ENTER}"),
        ("Tab",             "{TAB}"),
        ("Escape",          "{ESC}"),
        ("BackSpace",       "{BACKSPACE}"),
        ("Delete",          "{DELETE}"),
        ("Up",              "{UP}"),
        ("Down",            "{DOWN}"),
        ("Left",            "{LEFT}"),
        ("Right",           "{RIGHT}"),
        # Function keys
        ("F1",              "{F1}"),
        ("F12",             "{F12}"),
        # Modifier + char (prefix form)
        ("ctrl+c",          "^c"),
        ("ctrl+s",          "^s"),
        ("alt+F4",          "%{F4}"),
        ("shift+Tab",       "+{TAB}"),
        ("ctrl+shift+t",    "^+t"),
        # Modifier + special key
        ("ctrl+Return",     "^{ENTER}"),
        ("alt+Tab",         "%{TAB}"),
        # Super / Win — uses bracket form
        ("super+l",         "{LWIN down}l{LWIN up}"),
        ("win+e",           "{LWIN down}e{LWIN up}"),
        ("meta+d",          "{LWIN down}d{LWIN up}"),
        # Single chars
        ("a",               "a"),
        ("1",               "1"),
    ],
)
def test_xdotool_to_pywinauto_translation_table(xdotool, pywinauto):
    """The translation table covers every combo the existing code uses."""
    assert dc._xdotool_to_pywinauto_keys(xdotool) == pywinauto


def test_xdotool_to_pywinauto_super_and_modifier_combined():
    """Super + Ctrl + key — bracket modifier wraps the prefix+body."""
    assert dc._xdotool_to_pywinauto_keys("super+ctrl+l") == "{LWIN down}^l{LWIN up}"


def test_xdotool_to_pywinauto_empty_returns_empty():
    assert dc._xdotool_to_pywinauto_keys("") == ""
    assert dc._xdotool_to_pywinauto_keys("   ") == ""


# ---------------------------------------------------------------------------
# send_keys — Linux path
# ---------------------------------------------------------------------------


def test_send_keys_linux_uses_xdotool(force_linux, monkeypatch):
    """Linux backend invokes xdotool with --clearmodifiers + the raw combo."""
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.send_keys("Return") is True
    assert seen["argv"] == ["xdotool", "key", "--clearmodifiers", "Return"]


def test_send_keys_linux_combo(force_linux, monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.send_keys("ctrl+shift+t") is True
    assert seen["argv"][-1] == "ctrl+shift+t"


def test_send_keys_linux_returns_false_on_nonzero_exit(force_linux, monkeypatch):
    monkeypatch.setattr(
        dc.subprocess,
        "run",
        lambda *a, **k: mock.Mock(returncode=1, stdout="", stderr="no display"),
    )
    assert dc.send_keys("Return") is False


def test_send_keys_linux_returns_false_when_xdotool_missing(force_linux, monkeypatch):
    def fake_run(*a, **k):
        raise FileNotFoundError("xdotool")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.send_keys("Return") is False


# ---------------------------------------------------------------------------
# send_keys — Windows path
# ---------------------------------------------------------------------------


def test_send_keys_windows_uses_pywinauto(force_windows, fake_pywinauto):
    """Windows backend calls pywinauto.keyboard.send_keys with the translated combo."""
    assert dc.send_keys("Return") is True
    assert fake_pywinauto.keyboard.calls == [{"keys": "{ENTER}", "kwargs": {}}]


def test_send_keys_translates_super_l(force_windows, fake_pywinauto):
    """xdotool 'super+l' → pywinauto '{LWIN down}l{LWIN up}'."""
    assert dc.send_keys("super+l") is True
    assert fake_pywinauto.keyboard.calls[-1]["keys"] == "{LWIN down}l{LWIN up}"


def test_send_keys_translates_ctrl_c(force_windows, fake_pywinauto):
    """xdotool 'ctrl+c' → pywinauto '^c'."""
    assert dc.send_keys("ctrl+c") is True
    assert fake_pywinauto.keyboard.calls[-1]["keys"] == "^c"


def test_send_keys_translates_alt_f4(force_windows, fake_pywinauto):
    assert dc.send_keys("alt+F4") is True
    assert fake_pywinauto.keyboard.calls[-1]["keys"] == "%{F4}"


def test_send_keys_translates_shift_tab(force_windows, fake_pywinauto):
    assert dc.send_keys("shift+Tab") is True
    assert fake_pywinauto.keyboard.calls[-1]["keys"] == "+{TAB}"


def test_send_keys_windows_returns_false_on_pywinauto_exception(force_windows, fake_pywinauto):
    fake_pywinauto.keyboard.raise_exc = RuntimeError("boom")
    assert dc.send_keys("ctrl+c") is False


def test_send_keys_windows_returns_false_when_pywinauto_missing(force_windows, monkeypatch):
    """No pywinauto in sys.modules + import-fails → returns False."""
    # Ensure no fake_pywinauto leaks in — purge any cached pywinauto modules.
    for mod in list(sys.modules.keys()):
        if mod == "pywinauto" or mod.startswith("pywinauto."):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    # Force any future `from pywinauto import keyboard` to ImportError.
    import builtins
    real_import = builtins.__import__

    def banned_import(name, *a, **k):
        if name == "pywinauto" or name.startswith("pywinauto."):
            raise ImportError(f"banned: {name}")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", banned_import)
    assert dc.send_keys("Return") is False


def test_send_keys_empty_or_non_string_returns_false():
    assert dc.send_keys("") is False
    assert dc.send_keys(None) is False  # type: ignore[arg-type]
    assert dc.send_keys(42) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# type_text
# ---------------------------------------------------------------------------


def test_type_text_linux_uses_xdotool_type(force_linux, monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.type_text("hello world") is True
    # `--` terminates option parsing so a leading dash in text isn't a flag.
    assert seen["argv"] == ["xdotool", "type", "--clearmodifiers", "--", "hello world"]


def test_type_text_linux_empty_is_noop(force_linux, monkeypatch):
    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.type_text("") is True
    assert called["n"] == 0  # empty text shouldn't shell out


def test_type_text_windows_uses_pywinauto_with_spaces(force_windows, fake_pywinauto):
    assert dc.type_text("hello world") is True
    call = fake_pywinauto.keyboard.calls[-1]
    assert call["kwargs"].get("with_spaces") is True
    # Plain text without metacharacters round-trips unchanged.
    assert call["keys"] == "hello world"


def test_type_text_windows_escapes_metacharacters(force_windows, fake_pywinauto):
    """pywinauto's modifier chars (+, ^, %, ~, parens, braces) get wrapped in braces."""
    assert dc.type_text("a+b^c%d~e(f)g{h}") is True
    sent = fake_pywinauto.keyboard.calls[-1]["keys"]
    assert sent == "a{+}b{^}c{%}d{~}e{(}f{)}g{{}h{}}"


def test_type_text_returns_false_on_error(force_linux, monkeypatch):
    monkeypatch.setattr(
        dc.subprocess, "run",
        lambda *a, **k: mock.Mock(returncode=1, stdout="", stderr="fail"),
    )
    assert dc.type_text("hi") is False


def test_type_text_non_string_returns_false():
    assert dc.type_text(None) is False  # type: ignore[arg-type]
    assert dc.type_text(123) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# find_window_by_name
# ---------------------------------------------------------------------------


def test_find_window_linux_uses_xdotool(force_linux, monkeypatch):
    """Linux backend: xdotool search --name <pattern>; returns LAST id."""
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="1234567\n7654321\n", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    result = dc.find_window_by_name("Terminal")
    assert seen["argv"] == ["xdotool", "search", "--name", "Terminal"]
    # xdotool's stacking order — LAST line is most recent.
    assert result == 7654321


def test_find_window_linux_returns_none_on_no_match(force_linux, monkeypatch):
    monkeypatch.setattr(
        dc.subprocess, "run",
        lambda *a, **k: mock.Mock(returncode=1, stdout="", stderr=""),
    )
    assert dc.find_window_by_name("NothingHere") is None


def test_find_window_linux_returns_none_on_empty_stdout(force_linux, monkeypatch):
    monkeypatch.setattr(
        dc.subprocess, "run",
        lambda *a, **k: mock.Mock(returncode=0, stdout="\n\n", stderr=""),
    )
    assert dc.find_window_by_name("Terminal") is None


def test_find_window_windows_uses_pywinauto(force_windows, fake_pywinauto):
    fake_pywinauto.findwindows.result = [11111, 22222]
    result = dc.find_window_by_name("Notepad")
    assert result == 22222  # last match — parity with Linux semantics
    # Verify the pattern got escaped + wrapped as substring-style regex.
    call = fake_pywinauto.findwindows.calls[-1]
    assert call["kwargs"]["title_re"] == ".*Notepad.*"


def test_find_window_windows_returns_none_on_no_match(force_windows, fake_pywinauto):
    fake_pywinauto.findwindows.result = []
    assert dc.find_window_by_name("Ghost") is None


def test_find_window_windows_returns_none_on_exception(force_windows, fake_pywinauto):
    fake_pywinauto.findwindows.raise_exc = RuntimeError("no such window")
    assert dc.find_window_by_name("Anything") is None


def test_find_window_escapes_regex_metachars_on_windows(force_windows, fake_pywinauto):
    """A pattern containing regex meta (``.``, ``*``, ``?``) is treated literally."""
    fake_pywinauto.findwindows.result = [1]
    dc.find_window_by_name("foo.bar+baz*")
    call = fake_pywinauto.findwindows.calls[-1]
    # Every regex metachar should be re.escape'd.
    assert "foo\\.bar\\+baz\\*" in call["kwargs"]["title_re"]


def test_find_window_empty_pattern_returns_none():
    assert dc.find_window_by_name("") is None
    assert dc.find_window_by_name(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# activate_window
# ---------------------------------------------------------------------------


def test_activate_window_linux_uses_xdotool(force_linux, monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.activate_window(424242) is True
    assert seen["argv"] == ["xdotool", "windowactivate", "--sync", "424242"]


def test_activate_window_linux_returns_false_on_failure(force_linux, monkeypatch):
    monkeypatch.setattr(
        dc.subprocess, "run",
        lambda *a, **k: mock.Mock(returncode=1, stdout="", stderr="no such window"),
    )
    assert dc.activate_window(123) is False


def test_activate_window_windows_uses_pywinauto(force_windows, fake_pywinauto):
    assert dc.activate_window(99999) is True
    inst = fake_pywinauto.application_cls.instances[-1]
    assert inst.connected_handle == 99999
    assert "set_focus" in inst.actions


def test_activate_window_windows_returns_false_on_connect_failure(force_windows, fake_pywinauto):
    # First call: instance is created, connect raises.
    class _RaisingApp(fake_pywinauto.application_cls):
        def connect(self, **kwargs):
            raise RuntimeError("no such window")

    import pywinauto.application as app_mod  # the fake
    app_mod.Application = _RaisingApp  # type: ignore[attr-defined]
    assert dc.activate_window(404) is False


def test_activate_window_invalid_id_returns_false():
    assert dc.activate_window(0) is False
    assert dc.activate_window(-1) is False
    assert dc.activate_window("not-an-int") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# minimize_window / restore_window
# ---------------------------------------------------------------------------


def test_minimize_window_linux_uses_xdotool(force_linux, monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.minimize_window(555) is True
    assert seen["argv"] == ["xdotool", "windowminimize", "555"]


def test_minimize_window_windows_uses_pywinauto(force_windows, fake_pywinauto):
    assert dc.minimize_window(123) is True
    inst = fake_pywinauto.application_cls.instances[-1]
    assert inst.connected_handle == 123
    assert "minimize" in inst.actions


def test_restore_window_linux_uses_windowactivate(force_linux, monkeypatch):
    """Linux has no dedicated windowrestore — activate un-minimizes and raises."""
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    assert dc.restore_window(777) is True
    assert seen["argv"] == ["xdotool", "windowactivate", "777"]


def test_restore_window_windows_uses_pywinauto(force_windows, fake_pywinauto):
    assert dc.restore_window(321) is True
    inst = fake_pywinauto.application_cls.instances[-1]
    assert inst.connected_handle == 321
    assert "restore" in inst.actions


# ---------------------------------------------------------------------------
# xdotool_call — Linux-only escape hatch
# ---------------------------------------------------------------------------


def test_xdotool_call_linux_runs_xdotool(force_linux, monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return mock.Mock(returncode=0, stdout="winid_payload\n", stderr="")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    ok, out = dc.xdotool_call(["getactivewindow"])
    assert ok is True
    assert out == "winid_payload\n"
    assert seen["argv"] == ["xdotool", "getactivewindow"]


def test_xdotool_call_linux_returns_false_on_nonzero(force_linux, monkeypatch):
    monkeypatch.setattr(
        dc.subprocess, "run",
        lambda *a, **k: mock.Mock(returncode=1, stdout="", stderr="bad arg"),
    )
    ok, out = dc.xdotool_call(["bogus-cmd"])
    assert ok is False
    assert "bad arg" in out


def test_xdotool_call_returns_false_on_non_linux(force_windows):
    """Non-Linux returns (False, message) without crashing — no import of pywinauto."""
    ok, msg = dc.xdotool_call(["key", "Return"])
    assert ok is False
    assert "not available" in msg.lower() or "windows" in msg.lower()


def test_xdotool_call_rejects_non_list_args(force_linux):
    ok, msg = dc.xdotool_call("not-a-list")  # type: ignore[arg-type]
    assert ok is False
    assert "list" in msg.lower()


def test_xdotool_call_rejects_non_string_in_list(force_linux):
    ok, msg = dc.xdotool_call(["key", 42])  # type: ignore[list-item]
    assert ok is False


# ---------------------------------------------------------------------------
# Catch-all: helpers never raise
# ---------------------------------------------------------------------------


def test_all_functions_swallow_exceptions_on_linux(force_linux, monkeypatch):
    """If subprocess.run throws something exotic, helpers return sentinels — no raise."""

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(dc.subprocess, "run", boom)
    assert dc.send_keys("Return") is False
    assert dc.type_text("hi") is False
    assert dc.find_window_by_name("x") is None
    assert dc.activate_window(1) is False
    assert dc.minimize_window(1) is False
    assert dc.restore_window(1) is False
    ok, _ = dc.xdotool_call(["whatever"])
    assert ok is False


def test_all_functions_swallow_exceptions_on_windows(force_windows, fake_pywinauto):
    """Pywinauto raising during action returns sentinels — no raise."""
    # All fake_pywinauto interactions raise an unrelated RuntimeError.
    fake_pywinauto.keyboard.raise_exc = RuntimeError("nope")
    fake_pywinauto.findwindows.raise_exc = RuntimeError("nope")

    class _RaisingApp(fake_pywinauto.application_cls):
        def connect(self, **kwargs):
            raise RuntimeError("nope")

    import pywinauto.application as app_mod  # the fake
    app_mod.Application = _RaisingApp  # type: ignore[attr-defined]

    assert dc.send_keys("Return") is False
    assert dc.type_text("hi") is False
    assert dc.find_window_by_name("x") is None
    assert dc.activate_window(123) is False
    assert dc.minimize_window(123) is False
    assert dc.restore_window(123) is False


def test_helpers_are_all_exported():
    """The public surface is exactly seven names — keep contract stable."""
    expected = {
        "send_keys",
        "type_text",
        "find_window_by_name",
        "activate_window",
        "minimize_window",
        "restore_window",
        "xdotool_call",
    }
    assert set(dc.__all__) == expected
    for name in expected:
        assert callable(getattr(dc, name))
