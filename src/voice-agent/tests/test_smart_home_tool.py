import asyncio
import io
import urllib.error

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


# ---------------------------------------------------------------------------
# control — device resolution + intent mapping + speakable results
# ---------------------------------------------------------------------------

_HA_LIGHT = {
    "id": "ha:light.kitchen", "name": "Kitchen Light", "type": "light",
    "brand": "Home Assistant", "protocol": ["home_assistant"],
    "controllable": "local", "capabilities": ["on", "off", "brightness", "color"],
}
_HA_CLIMATE = {
    "id": "ha:climate.living_room", "name": "Living Room", "type": "thermostat",
    "brand": "Home Assistant", "protocol": ["home_assistant"],
    "controllable": "local", "capabilities": ["set_temperature"],
}
_WEBOS_TV = {
    "id": "", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.50",
    "name": "LG TV", "type": "tv", "brand": "LG webOS", "protocol": ["ssdp"],
    "controllable": "local", "control_hint": "webOS",
    "capabilities": ["power", "volume", "mute", "apps", "inputs", "media"],
}
_ECHO = {
    "name": "Echo Dot", "type": "speaker", "brand": "Amazon",
    "ip": "192.168.1.60", "controllable": "cloud_only", "control_hint": "Alexa app",
    "capabilities": [],
}


def _run_control(monkeypatch, devices, req, status=200, body=None):
    """Drive a control call with the sidecar HTTP mocked; → (spoken, posts)."""
    posts = []
    monkeypatch.setattr(smart_home, "_fetch_devices", lambda: devices)

    def fake_post(key, payload):
        posts.append((key, payload))
        return status, (body if body is not None else {"ok": True})

    monkeypatch.setattr(smart_home, "_post_command", fake_post)
    out = asyncio.run(smart_home.handle_smart_home({"action": "control", **req}))
    return out, posts


def test_control_light_on_speakable_confirmation(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_HA_LIGHT, _WEBOS_TV, _ECHO],
        {"query": "kitchen light", "command": "on"})
    assert posts == [("ha:light.kitchen", {"action": "on"})]
    assert out == "Kitchen Light is on."


def test_control_brightness_maps_to_pct(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_HA_LIGHT], {"query": "kitchen light", "command": "brightness",
                                   "value": 40})
    assert posts == [("ha:light.kitchen", {"action": "brightness", "brightness_pct": 40})]
    assert "40 percent" in out


def test_control_tv_mute_uses_webos_dialect_and_mac_key(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_HA_LIGHT, _WEBOS_TV], {"query": "the TV", "command": "mute"})
    assert posts == [("aa:bb:cc:dd:ee:ff", {"action": "mute", "mute": True})]
    assert "muted" in out.lower()


def test_control_tv_on_maps_to_power_on(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_WEBOS_TV], {"query": "tv", "command": "on"},
        body={"ok": True, "wol_sent": True, "connected": False})
    assert posts == [("aa:bb:cc:dd:ee:ff", {"action": "power_on"})]
    assert "wake" in out.lower()  # WoL sent, TV still booting — say so


def test_control_volume_up_on_tv(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_WEBOS_TV], {"query": "tv", "command": "volume_up"})
    assert posts == [("aa:bb:cc:dd:ee:ff", {"action": "volume_up"})]
    assert "volume up" in out.lower()


def test_control_set_temperature_speaks_degrees(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_HA_CLIMATE, _HA_LIGHT],
        {"query": "living room", "command": "set_temperature", "value": 22})
    assert posts == [("ha:climate.living_room",
                      {"action": "set_temperature", "temperature": 22.0})]
    assert "22 degrees" in out


def test_control_color_maps_name_to_rgb(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_HA_LIGHT],
        {"query": "kitchen light", "command": "color", "color": "blue"})
    assert posts == [("ha:light.kitchen", {"action": "color", "rgb_color": [0, 0, 255]})]
    assert "blue" in out.lower()


def test_control_ambiguous_returns_clarify_list(monkeypatch):
    bedroom = dict(_HA_LIGHT, id="ha:light.bedroom", name="Bedroom Light")
    out, posts = _run_control(
        monkeypatch, [_HA_LIGHT, bedroom], {"query": "light", "command": "on"})
    assert posts == []  # no command fired while ambiguous
    assert "which one" in out.lower()
    assert "kitchen light" in out.lower() and "bedroom light" in out.lower()


def test_control_no_match_is_speakable(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_HA_LIGHT], {"query": "sauna", "command": "on"})
    assert posts == []
    assert "don't see" in out.lower() and "sauna" in out.lower()


def test_control_rejects_unsupported_capability(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_HA_LIGHT], {"query": "kitchen light", "command": "mute"})
    assert posts == []  # capability gate fires before any HTTP
    assert "can't" in out.lower() or "doesn't" in out.lower()


def test_control_cloud_only_is_refused_speakably(monkeypatch):
    out, posts = _run_control(
        monkeypatch, [_ECHO], {"query": "echo", "command": "on"})
    assert posts == []
    assert "cloud-only" in out.lower() and "alexa app" in out.lower()


def test_control_428_speaks_pairing_guidance(monkeypatch):
    out, _ = _run_control(
        monkeypatch, [_WEBOS_TV], {"query": "tv", "command": "mute"},
        status=428, body={"ok": False, "error": "TV not paired"})
    assert "pair" in out.lower() and "devices page" in out.lower()


def test_control_424_speaks_ha_config_guidance(monkeypatch):
    out, _ = _run_control(
        monkeypatch, [_HA_LIGHT], {"query": "kitchen light", "command": "on"},
        status=424, body={"ok": False, "error": "HA not configured"})
    assert "home assistant" in out.lower() and "devices page" in out.lower()


def test_control_sidecar_error_body_is_spoken(monkeypatch):
    out, _ = _run_control(
        monkeypatch, [_WEBOS_TV], {"query": "tv", "command": "pause"},
        status=502, body={"ok": False, "error": "could not reach the TV at 192.168.1.50"})
    assert "could not reach the tv" in out.lower()


def test_post_command_parses_http_error_json_body(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 428, "Precondition Required", None,
            io.BytesIO(b'{"ok": false, "error": "TV not paired"}'))

    monkeypatch.setattr(smart_home.urllib.request, "urlopen", fake_urlopen)
    status, body = smart_home._post_command("aa:bb:cc:dd:ee:ff", {"action": "mute"})
    assert status == 428
    assert body == {"ok": False, "error": "TV not paired"}
