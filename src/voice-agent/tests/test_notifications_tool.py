"""Tests for the `notifications` registry tool (reads the captured store)."""
import json

import pipeline.notification_store as ns


def test_handle_reads_store_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ns.append("Slack", "Old", "")
    ns.append("Mail", "New message", "Hey there")
    from tools import notifications  # import triggers registry.register
    out = json.loads(notifications._handle_notifications({"limit": 5}))
    assert out["count"] == 2
    assert out["notifications"][0]["app"] == "Mail"
    assert out["notifications"][0]["summary"] == "New message"
    assert out["notifications"][0]["body"] == "Hey there"
    assert "ago" in out["notifications"][0]["age"]


def test_handle_empty_store(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "empty"))
    from tools import notifications
    out = json.loads(notifications._handle_notifications({}))
    assert out == {"count": 0, "notifications": []}


def test_tool_is_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from tools import notifications  # noqa: F401
    from tools.registry import registry
    # The registry exposes registered tool names; importing the module above
    # must have registered "notifications" without raising.
    names = {getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
             for t in getattr(registry, "_tools", {}).values()} if hasattr(registry, "_tools") else set()
    # Fall back to a direct lookup helper if present.
    assert ("notifications" in names) or (getattr(registry, "get", lambda *_: None)("notifications") is not None)
