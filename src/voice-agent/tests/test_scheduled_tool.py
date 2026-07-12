"""Unit checks for the `scheduled` voice tool's pure helpers (no network)."""
import os

os.environ.setdefault("JARVIS_SCHEDULED_VOICE_TOKEN", "test-token")

from tools import scheduled  # noqa: E402


def test_instances_dedup_and_default():
    os.environ.pop("JARVIS_SCHEDULED_WEB_URLS", None)
    os.environ.pop("JARVIS_SCHEDULED_WEB_URL", None)
    inst = scheduled._instances()
    assert "https://0wlan.com" in inst and "http://127.0.0.1:3000" in inst
    os.environ["JARVIS_SCHEDULED_WEB_URLS"] = "https://a.com/, https://a.com , http://b:3000"
    try:
        assert scheduled._instances() == ["https://a.com", "http://b:3000"]  # dedup + strip
    finally:
        os.environ.pop("JARVIS_SCHEDULED_WEB_URLS", None)


def test_match_across_instances():
    pairs = [
        ("https://0wlan.com", {"id": "1", "name": "Morning Brief"}),
        ("http://127.0.0.1:3000", {"id": "2", "name": "Weekly Review"}),
    ]
    url, t = scheduled._match(pairs, "morning brief")  # exact, case-insensitive
    assert url == "https://0wlan.com" and t["id"] == "1"
    url, t = scheduled._match(pairs, "weekly")  # substring → home instance
    assert url == "http://127.0.0.1:3000" and t["id"] == "2"
    assert scheduled._match(pairs, "nope") is None
    assert scheduled._match(pairs, "  ") is None


def test_fmt_list_tags_instance_when_multi():
    single = scheduled._fmt_list([("https://0wlan.com", {"name": "X", "label": "Daily"})])
    assert "X" in single and "[cloud]" not in single  # no tag when one instance
    multi = scheduled._fmt_list([
        ("https://0wlan.com", {"name": "A", "label": "Daily"}),
        ("http://127.0.0.1:3000", {"name": "B", "label": "Weekly"}),
    ])
    assert "[cloud]" in multi and "[local]" in multi
    assert "don't have any" in scheduled._fmt_list([]).lower()


def test_configured_gate():
    assert scheduled._configured() is True
    old = os.environ.pop("JARVIS_SCHEDULED_VOICE_TOKEN", None)
    try:
        assert scheduled._configured() is False
    finally:
        if old is not None:
            os.environ["JARVIS_SCHEDULED_VOICE_TOKEN"] = old
