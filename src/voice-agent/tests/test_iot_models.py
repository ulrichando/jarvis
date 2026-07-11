from iot.models import Observation, Device, Controllable


def test_observation_defaults():
    o = Observation(source="mdns", ip="192.168.1.5")
    assert o.source == "mdns" and o.ip == "192.168.1.5"
    assert o.mac is None and o.data == {}


def test_device_key_prefers_mac_then_ip():
    assert Device(ip="1.2.3.4", mac="aa:bb:cc:dd:ee:ff").key == "aa:bb:cc:dd:ee:ff"
    assert Device(ip="1.2.3.4", mac=None).key == "ip:1.2.3.4"


def test_device_to_dict_roundtrips():
    d = Device(ip="1.2.3.4", mac="aa:bb:cc:dd:ee:ff", name="Roku",
               type="tv", brand="Roku", controllable=Controllable.LOCAL,
               control_hint="Roku ECP")
    j = d.to_dict()
    assert j["controllable"] == "local"
    assert Device.from_dict(j).to_dict() == j
