"""Tests for tools/computer_safety.py — destructive-intent detection +
password-field detection (AT-SPI primary, Gemini fallback)."""
import pytest

from tools.computer_atspi import Widget


def _widget(role, text, x=0, y=0, w=80, h=30):
    return Widget(
        role=role, bounds=(x, y, w, h), text=text,
        enabled=True, active=False,
    )


# ── parse_destructive_intent ──


def test_parse_destructive_intent_click_on_delete_button():
    from tools.computer_safety import parse_destructive_intent
    widgets = [_widget("push_button", "Delete", x=300, y=200)]
    action = {"action": "left_click", "coordinate": [340, 215]}
    result = parse_destructive_intent(action, widgets)
    assert result is not None
    assert "Delete" in result


def test_parse_destructive_intent_click_misses_safe_button():
    from tools.computer_safety import parse_destructive_intent
    widgets = [_widget("push_button", "Preview", x=300, y=200)]
    action = {"action": "left_click", "coordinate": [340, 215]}
    assert parse_destructive_intent(action, widgets) is None


def test_parse_destructive_intent_destructive_shell_in_type():
    from tools.computer_safety import parse_destructive_intent
    action = {"action": "type", "text": "rm -rf /tmp/foo"}
    result = parse_destructive_intent(action, widgets=[])
    assert result is not None
    assert "rm" in result.lower() or "destructive" in result.lower()


def test_parse_destructive_intent_safe_type():
    from tools.computer_safety import parse_destructive_intent
    action = {"action": "type", "text": "hello world"}
    assert parse_destructive_intent(action, widgets=[]) is None


def test_parse_destructive_intent_screenshot_is_safe():
    from tools.computer_safety import parse_destructive_intent
    action = {"action": "screenshot"}
    assert parse_destructive_intent(action, widgets=[]) is None


@pytest.mark.parametrize("verb", [
    "delete", "Send", "Submit", "Overwrite", "Format",
    "Remove", "Erase", "Discard", "Publish", "Post", "Drop", "Wipe",
])
def test_every_destructive_verb_detected(verb):
    from tools.computer_safety import parse_destructive_intent
    widgets = [_widget("push_button", verb, x=300, y=200)]
    action = {"action": "left_click", "coordinate": [340, 215]}
    assert parse_destructive_intent(action, widgets) is not None, (
        f"verb {verb!r} should trigger confirmation"
    )


# ── is_password_field_visible ──


@pytest.mark.asyncio
async def test_password_visible_via_atspi():
    from tools.computer_safety import is_password_field_visible
    widgets = [_widget("password_text", "", x=0, y=0)]
    assert await is_password_field_visible(png=b"", widgets=widgets) is True


@pytest.mark.asyncio
async def test_password_not_visible_without_password_widget(monkeypatch):
    from tools.computer_safety import is_password_field_visible
    # No password_text in widgets AND Gemini fallback returns False
    from tools import computer_safety
    async def fake_gemini(png):
        return False
    monkeypatch.setattr(
        computer_safety, "_gemini_password_check", fake_gemini
    )
    widgets = [_widget("text", "user@example.com")]
    assert await is_password_field_visible(png=b"img", widgets=widgets) is False


@pytest.mark.asyncio
async def test_password_visible_via_gemini_fallback(monkeypatch):
    """When AT-SPI returned empty (canvas app), fall back to Gemini."""
    from tools.computer_safety import is_password_field_visible
    from tools import computer_safety
    async def fake_gemini(png):
        return True
    monkeypatch.setattr(
        computer_safety, "_gemini_password_check", fake_gemini
    )
    assert await is_password_field_visible(png=b"img", widgets=[]) is True


# ── check_password_visible (2026-05-18 fail-open hardening) ──

@pytest.mark.asyncio
async def test_check_password_visible_fastpath_hit():
    """AT-SPI password_text widget → instant return (no Gemini call)."""
    from tools.computer_safety import check_password_visible
    widgets = [_widget("password_text", "")]
    visible, state = await check_password_visible(png=b"", widgets=widgets)
    assert visible is True
    assert state == "fastpath_hit"


@pytest.mark.asyncio
async def test_check_password_visible_fastpath_miss():
    """AT-SPI returned other widgets but no password_text → instant False."""
    from tools.computer_safety import check_password_visible
    widgets = [_widget("text", "user@example.com")]
    visible, state = await check_password_visible(png=b"", widgets=widgets)
    assert visible is False
    assert state == "fastpath_miss"


@pytest.mark.asyncio
async def test_check_password_visible_slowpath_success(monkeypatch):
    """AT-SPI empty + Gemini returns True quickly → state='slowpath'."""
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def fast_gemini(png):
        return True
    monkeypatch.setattr(computer_safety, "_gemini_password_check", fast_gemini)
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is True
    assert state == "slowpath"


@pytest.mark.asyncio
async def test_check_password_visible_failopen_on_timeout(monkeypatch):
    """AT-SPI empty + Gemini hangs past timeout → fail OPEN (False, 'failopen')."""
    import asyncio
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def slow_gemini(png):
        await asyncio.sleep(10.0)
        return True
    monkeypatch.setattr(computer_safety, "_gemini_password_check", slow_gemini)
    monkeypatch.setattr(computer_safety, "_GEMINI_TIMEOUT_S", 0.05)
    monkeypatch.delenv("JARVIS_PASSWORD_CHECK_STRICT", raising=False)
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is False  # default: fail-open
    assert state == "failopen"


@pytest.mark.asyncio
async def test_check_password_visible_failopen_strict_mode(monkeypatch):
    """STRICT=1 + timeout → fail CLOSED (returns True so loop bails)."""
    import asyncio
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def slow_gemini(png):
        await asyncio.sleep(10.0)
        return False
    monkeypatch.setattr(computer_safety, "_gemini_password_check", slow_gemini)
    monkeypatch.setattr(computer_safety, "_GEMINI_TIMEOUT_S", 0.05)
    monkeypatch.setenv("JARVIS_PASSWORD_CHECK_STRICT", "1")
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is True  # strict: fail-closed
    assert state == "failopen"


@pytest.mark.asyncio
async def test_check_password_visible_failopen_on_exception(monkeypatch):
    """AT-SPI empty + Gemini raises → fail-open (default mode)."""
    from tools.computer_safety import check_password_visible
    from tools import computer_safety
    async def broken_gemini(png):
        raise RuntimeError("provider unreachable")
    monkeypatch.setattr(computer_safety, "_gemini_password_check", broken_gemini)
    monkeypatch.delenv("JARVIS_PASSWORD_CHECK_STRICT", raising=False)
    visible, state = await check_password_visible(png=b"img", widgets=[])
    assert visible is False
    assert state == "failopen"
