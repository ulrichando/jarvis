from iot.models import Observation, Controllable
from iot.identify import identify


def test_roku_by_ssdp():
    d = identify([Observation(source="ssdp", ip="192.168.1.9", service="roku:ecp")])
    assert d.type == "tv" and d.brand == "Roku"
    assert d.controllable == Controllable.LOCAL and "Roku" in d.control_hint


def test_alexa_is_cloud_only():
    d = identify([Observation(source="mdns", ip="192.168.1.20", service="_amzn-wplay._tcp")])
    assert d.brand == "Amazon Alexa"
    assert d.controllable == Controllable.CLOUD_ONLY


def test_tuya_bulb_cloud_only():
    d = identify([Observation(source="tuya", ip="192.168.1.30", data={"gwId": "abc"})])
    assert d.type == "light" and d.controllable == Controllable.CLOUD_ONLY


def test_lg_webos_tv_not_misidentified_as_cast():
    # Real fingerprint of the LG OLED at .245 — advertises _googlecast, so the
    # generic Cast rule used to win and mis-brand it "Google Cast".
    d = identify([
        Observation(source="mdns", ip="192.168.1.245", service="_googlecast._tcp",
                    data={"fn": "[LG] webOS TV OLED77C5PUA"}),
        Observation(source="mdns", ip="192.168.1.245", service="_airplay._tcp",
                    data={"manufacturer": "LG", "model": "OLED77C5PUA"}),
        Observation(source="ssdp", ip="192.168.1.245",
                    data={"server": "Linux/5.15 UPnP/1.0 WebOS/4.1.0 DLNADOC/1.50"}),
    ])
    assert d.brand == "LG webOS" and d.type == "tv"
    assert d.controllable == Controllable.LOCAL and d.control_hint == "LG webOS"


def test_lg_webos_via_airplay_manufacturer_alone():
    d = identify([Observation(source="mdns", ip="192.168.1.245", service="_airplay._tcp",
                              data={"manufacturer": "LG Electronics"})])
    assert d.brand == "LG webOS" and d.type == "tv"


def test_plain_chromecast_still_google_cast():
    d = identify([Observation(source="mdns", ip="192.168.1.50", service="_googlecast._tcp",
                              data={"fn": "Living Room speaker", "md": "Google Nest Mini"})])
    assert d.brand == "Google Cast"


def test_unknown_keeps_mac_vendor_brand():
    d = identify([Observation(source="arp", ip="192.168.1.40", mac="aa:bb:cc:00:00:00",
                              data={"vendor": "Acme Inc"})])
    assert d.brand == "Acme Inc" and d.controllable == Controllable.UNKNOWN
