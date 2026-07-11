import asyncio

from tools import smart_home


def test_speakable_summary_groups_and_flags_control(monkeypatch):
    fake = {"devices": [
        {"name": "Roku TV", "type": "tv", "controllable": "local"},
        {"name": "Echo Dot", "type": "speaker", "controllable": "cloud_only"},
        {"name": "Bulb", "type": "light", "controllable": "cloud_only"},
    ]}
    monkeypatch.setattr(smart_home, "_fetch_devices", lambda: fake["devices"])
    out = asyncio.run(smart_home.handle_smart_home({"action": "list_devices"}))
    assert "3 device" in out.lower()
    assert "roku" in out.lower()
    # truthful about control
    assert "can't control" in out.lower() or "not" in out.lower()
