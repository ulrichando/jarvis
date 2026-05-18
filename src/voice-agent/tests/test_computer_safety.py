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
