from iot.netfilter import is_lan_device_ip


def test_keeps_real_lan_ips():
    assert is_lan_device_ip("192.168.1.50")
    assert is_lan_device_ip("10.0.0.5")
    assert is_lan_device_ip("192.168.1.254")  # gateway is still a real LAN device


def test_drops_virtual_and_self():
    assert not is_lan_device_ip("127.0.0.1")          # loopback
    assert not is_lan_device_ip("169.254.10.10")      # link-local
    assert not is_lan_device_ip("100.113.64.252")     # tailscale / CGNAT
    assert not is_lan_device_ip("172.17.0.2")         # docker bridge
    assert not is_lan_device_ip("0.0.0.0")            # unspecified
    assert not is_lan_device_ip("not-an-ip")          # garbage
