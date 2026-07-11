import asyncio

from iot.models import Observation
from iot.scanners.arp import ArpScanner


def test_arp_parses_table_and_resolves_vendor():
    sample = "192.168.1.5 dev wlan0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n" \
             "192.168.1.9 dev wlan0 lladdr 11:22:33:44:55:66 STALE\n"
    scanner = ArpScanner(read_table=lambda: sample,
                         vendor_of=lambda mac: "Amazon" if mac.startswith("aa") else "")
    obs = asyncio.run(scanner.scan(timeout=1))
    by_ip = {o.ip: o for o in obs}
    assert by_ip["192.168.1.5"].mac == "aa:bb:cc:dd:ee:ff"
    assert by_ip["192.168.1.5"].data["vendor"] == "Amazon"
    assert by_ip["192.168.1.9"].data["vendor"] == ""
