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
    # Precision-first: a bare ARP host keeps its vendor brand but is now
    # EXCLUDED by default (no smart-home service advertised).
    d = identify([Observation(source="arp", ip="192.168.1.40", mac="aa:bb:cc:00:00:00",
                              data={"vendor": "Acme Inc"})])
    assert d.brand == "Acme Inc" and d.controllable == Controllable.UNKNOWN
    assert d.excluded and d.exclude_reason == "no smart-home service advertised"


# ── precision-first exclusion pass ──────────────────────────────────────────

def test_android_phone_excluded():
    # An Android phone: _companion-link-style co-signal + phone hostname + a
    # stray _googlecast (casting SENDER). The Cast tiebreaker must NOT rescue it.
    d = identify([
        Observation(source="mdns", ip="192.168.1.77", service="_companion-link._tcp",
                    hostname="Android-3.local."),
        Observation(source="mdns", ip="192.168.1.77", service="_googlecast._tcp",
                    hostname="Android-3.local."),
    ])
    assert d.excluded and "phone" in d.exclude_reason
    assert d.type == "unknown"


def test_android_phone_excluded_by_hostname_alone():
    d = identify([Observation(source="mdns", ip="192.168.1.78", service="_googlecast._tcp",
                              hostname="Android.local.")])
    assert d.excluded and "phone" in d.exclude_reason


def test_iphone_airplay_excluded():
    # iPhones advertise AirPlay/RAOP as casting senders — the phone co-signal wins.
    d = identify([
        Observation(source="mdns", ip="192.168.1.60", service="_airplay._tcp"),
        Observation(source="mdns", ip="192.168.1.60", service="_raop._tcp"),
        Observation(source="mdns", ip="192.168.1.60", service="_companion-link._tcp"),
    ])
    assert d.excluded and "phone" in d.exclude_reason
    assert d.type != "speaker"


def test_apple_tv_kept():
    d = identify([
        Observation(source="mdns", ip="192.168.1.61", service="_airplay._tcp",
                    data={"model": "AppleTV14,1"}),
        Observation(source="mdns", ip="192.168.1.61", service="_raop._tcp"),
    ])
    assert not d.excluded
    assert d.type == "tv" and d.controllable == Controllable.LOCAL


def test_airplay_receiver_without_co_services_kept():
    d = identify([Observation(source="mdns", ip="192.168.1.62", service="_raop._tcp")])
    assert not d.excluded and d.type == "speaker"


def test_mac_excluded():
    d = identify([
        Observation(source="mdns", ip="192.168.1.70", service="_ssh._tcp"),
        Observation(source="mdns", ip="192.168.1.70", service="_smb._tcp"),
        Observation(source="mdns", ip="192.168.1.70", service="_device-info._tcp",
                    data={"model": "MacBookPro18,3"}),
    ])
    assert d.excluded and "computer" in d.exclude_reason


def test_mac_airplay_sender_excluded():
    # A Mac running AirPlay + file sharing is a computer, not a speaker.
    d = identify([
        Observation(source="mdns", ip="192.168.1.71", service="_airplay._tcp"),
        Observation(source="mdns", ip="192.168.1.71", service="_smb._tcp"),
    ])
    assert d.excluded and "computer" in d.exclude_reason


def test_printer_excluded():
    d = identify([Observation(source="mdns", ip="192.168.1.80", service="_ipp._tcp")])
    assert d.excluded and "printer" in d.exclude_reason


def test_printer_excluded_by_port_9100():
    d = identify([Observation(source="arp", ip="192.168.1.81", mac="00:11:22:33:44:55",
                              port=9100)])
    assert d.excluded and "printer" in d.exclude_reason


def test_router_excluded_by_gateway():
    d = identify([Observation(source="arp", ip="192.168.1.1", mac="de:ad:be:ef:00:01")],
                 gateway_ip="192.168.1.1")
    assert d.excluded and "router" in d.exclude_reason


def test_router_excluded_by_ssdp_igd():
    d = identify([Observation(source="ssdp", ip="192.168.1.254",
                              service="urn:schemas-upnp-org:device:InternetGatewayDevice:1")])
    assert d.excluded and "router" in d.exclude_reason


def test_bare_arp_host_excluded():
    d = identify([Observation(source="arp", ip="192.168.1.90", mac="12:34:56:78:9a:bc")])
    assert d.excluded and d.exclude_reason == "no smart-home service advertised"


def test_lg_tv_survives_coincident_exclude():
    # A strong positive outranks a stray exclude co-signal (research precedence).
    d = identify([
        Observation(source="mdns", ip="192.168.1.245", service="_googlecast._tcp",
                    data={"fn": "[LG] webOS TV OLED77C5PUA"}),
        Observation(source="mdns", ip="192.168.1.245", service="_smb._tcp"),
    ])
    assert not d.excluded and d.type == "tv" and d.brand == "LG webOS"


def test_androidtvremote2_alone_is_tv():
    # TV-only signal — Android phones never advertise it.
    d = identify([Observation(source="mdns", ip="192.168.1.91",
                              service="_androidtvremote2._tcp")])
    assert not d.excluded
    assert d.type == "tv" and d.controllable == Controllable.LOCAL


def test_homekit_accessory_visible_as_unknown_smart():
    # unknown-but-smart stays VISIBLE — excluded is a separate axis from type.
    d = identify([Observation(source="mdns", ip="192.168.1.92", service="_hap._tcp")])
    assert not d.excluded
    assert d.type == "unknown" and d.brand == "HomeKit"


def test_chromecast_speaker_md_maps_to_speaker():
    d = identify([Observation(source="mdns", ip="192.168.1.50", service="_googlecast._tcp",
                              data={"fn": "Living Room speaker", "md": "Google Nest Mini"})])
    assert not d.excluded and d.type == "speaker" and d.brand == "Google Cast"
