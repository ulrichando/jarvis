import asyncio

from tools import smart_home


def test_speakable_leads_with_named_and_flags_control(monkeypatch):
    fake = [
        {"name": "Roku TV", "type": "tv", "controllable": "local"},
        {"name": "Echo Dot", "type": "speaker", "controllable": "cloud_only"},
        {"name": "Bulb", "type": "light", "controllable": "cloud_only"},
    ]
    monkeypatch.setattr(smart_home, "_fetch_devices", lambda: fake)
    out = asyncio.run(smart_home.handle_smart_home({"action": "list_devices"})).lower()
    assert "3 smart device" in out
    assert "roku tv" in out
    # truthful about control — the cloud-only ones can't be commanded
    assert "not command" in out or "can only see" in out


def test_speakable_folds_unknown_hosts_and_hides_raw_ips(monkeypatch):
    fake = [
        {"name": "Cast TV", "type": "tv", "brand": "Google Cast", "controllable": "local"},
        {"name": "192.168.1.254", "type": "unknown", "controllable": "unknown"},
        {"name": "10.0.0.9", "type": "unknown", "controllable": "unknown"},
    ]
    monkeypatch.setattr(smart_home, "_fetch_devices", lambda: fake)
    out = asyncio.run(smart_home.handle_smart_home({"action": "list_devices"})).lower()
    assert "1 smart device" in out and "cast tv" in out
    assert "2 unidentified host" in out
    # the unknown hosts' raw IPs are NOT read out in the lead summary
    assert "192.168.1.254" not in out and "10.0.0.9" not in out


def test_find_device_filters(monkeypatch):
    fake = [
        {"name": "Roku TV", "type": "tv", "controllable": "local"},
        {"name": "Echo Dot", "type": "speaker", "controllable": "cloud_only"},
    ]
    monkeypatch.setattr(smart_home, "_fetch_devices", lambda: fake)
    out = asyncio.run(
        smart_home.handle_smart_home({"action": "find_device", "query": "roku"})).lower()
    assert "roku tv" in out and "echo" not in out
