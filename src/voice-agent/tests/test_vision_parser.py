"""Vision LLM call + JSON parsing into ScreenFact.

Mocks the Moonshot HTTP client so tests don't hit the API.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("KIMI_API_KEY", "test-key")


def _fake_moonshot_response(content: str):
    """Build a fake OpenAI-shaped response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": content},
        }],
    }
    return resp


def test_describe_screen_parses_well_formed_json():
    from vision_tap import describe_screen

    fake_json = (
        '{"active_app": "chrome", "foreground_url": '
        '"https://youtube.com", "tab_count": 3, '
        '"dom_summary": "YouTube homepage"}'
    )
    with patch("vision_tap.requests.post",
               return_value=_fake_moonshot_response(fake_json)):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is not None
    assert fact.active_app == "chrome"
    assert fact.tab_count == 3


def test_describe_screen_handles_uncertain_response():
    from vision_tap import describe_screen
    fake_json = '{"active_app": null, "uncertain": true, "reason": "blank screen"}'
    with patch("vision_tap.requests.post",
               return_value=_fake_moonshot_response(fake_json)):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is not None
    assert fact.uncertain is True


def test_describe_screen_returns_none_on_invalid_json():
    """Vision LLM may return Chinese, garbage, or refuse. Parser
    must not crash."""
    from vision_tap import describe_screen
    with patch("vision_tap.requests.post",
               return_value=_fake_moonshot_response("blah blah not json")):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is None


def test_describe_screen_returns_none_on_http_error():
    from vision_tap import describe_screen
    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.json.return_value = {}
    err_resp.text = "internal server error"
    with patch("vision_tap.requests.post", return_value=err_resp):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is None
