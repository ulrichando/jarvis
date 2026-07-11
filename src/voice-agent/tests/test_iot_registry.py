from iot.models import Device, Controllable
from iot.registry import DeviceRegistry


def test_merge_by_key_updates_last_seen(tmp_path):
    reg = DeviceRegistry(path=tmp_path / "iot.json")
    reg.upsert(Device(ip="1.2.3.4", mac="aa:bb:cc:dd:ee:ff", name="Roku"))
    reg.upsert(Device(ip="1.2.3.9", mac="aa:bb:cc:dd:ee:ff", name="Roku"))  # same MAC, new IP
    assert len(reg.all()) == 1
    assert reg.all()[0].ip == "1.2.3.9"  # newest wins


def test_persist_and_reload(tmp_path):
    p = tmp_path / "iot.json"
    reg = DeviceRegistry(path=p)
    reg.upsert(Device(ip="1.2.3.4", mac="m1", controllable=Controllable.LOCAL))
    reg2 = DeviceRegistry(path=p)
    assert len(reg2.all()) == 1 and reg2.all()[0].controllable == Controllable.LOCAL
