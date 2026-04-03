"""JARVIS System Agents — specialized agents for full machine control.

Each agent is a function that takes a task description and executes it
using direct system calls. No LLM tool-calling needed — each agent
knows exactly how to do its job.

Agents:
- TerminalAgent: run commands, scripts, pipes
- InputAgent: keyboard, mouse, clipboard
- AppAgent: launch, close, manage applications
- SystemAgent: services, packages, users, permissions
- NetworkAgent: connections, scanning, firewall
- FileAgent: read, write, search, manage files
- DesktopAgent: windows, screenshots, volume, display
"""

import os
import subprocess
import shlex
import time

DISPLAY = os.environ.get("DISPLAY", ":0.0")
HOME = os.path.expanduser("~")
ENV = {
    **os.environ,
    "DISPLAY": DISPLAY,
    "XAUTHORITY": os.environ.get("XAUTHORITY", f"{HOME}/.Xauthority"),
    "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
}


def _run(cmd: str, timeout: int = 60) -> dict:
    """Run a shell command and return structured result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=HOME, env=ENV,
        )
        return {
            "success": result.returncode == 0,
            "output": (result.stdout + result.stderr).strip(),
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": f"Timed out after {timeout}s", "exit_code": -1}
    except Exception as e:
        return {"success": False, "output": str(e), "exit_code": -1}


# ══════════════════════════════════════════════════════════════════════
# TERMINAL AGENT — raw command execution
# ══════════════════════════════════════════════════════════════════════

class TerminalAgent:
    """Execute shell commands with full root access."""

    name = "terminal"

    @staticmethod
    def run(command: str, timeout: int = 60) -> dict:
        """Run a single command."""
        return _run(command, timeout)

    @staticmethod
    def run_script(commands: list[str], timeout: int = 120) -> dict:
        """Run multiple commands sequentially."""
        outputs = []
        for cmd in commands:
            r = _run(cmd, timeout)
            outputs.append(f"$ {cmd}\n{r['output']}")
            if not r["success"]:
                break
        return {
            "success": all("exit_code=0" not in o or True for o in outputs),
            "output": "\n\n".join(outputs),
        }

    @staticmethod
    def run_sudo(command: str, timeout: int = 60) -> dict:
        """Run a command with sudo."""
        return _run(f"sudo {command}", timeout)

    @staticmethod
    def run_background(command: str) -> dict:
        """Run a command in background (detached)."""
        return _run(f"nohup {command} > /dev/null 2>&1 &")

    @staticmethod
    def get_output(command: str, timeout: int = 30) -> str:
        """Run a command and return just the output string."""
        r = _run(command, timeout)
        return r["output"]


# ══════════════════════════════════════════════════════════════════════
# INPUT AGENT — keyboard, mouse, clipboard
# ══════════════════════════════════════════════════════════════════════

class InputAgent:
    """Control keyboard, mouse, and clipboard via xdotool/xclip."""

    name = "input"

    @staticmethod
    def type_text(text: str, delay: int = 50) -> dict:
        """Type text as if from keyboard."""
        return _run(f"xdotool type --delay {delay} {shlex.quote(text)}")

    @staticmethod
    def press_key(key: str) -> dict:
        """Press a key or key combo (e.g. 'ctrl+c', 'Return', 'alt+F4')."""
        return _run(f"xdotool key {key}")

    @staticmethod
    def hotkey(keys: str) -> dict:
        """Press a keyboard shortcut (e.g. 'ctrl+shift+t')."""
        return _run(f"xdotool key {keys}")

    @staticmethod
    def mouse_move(x: int, y: int) -> dict:
        """Move mouse to absolute position."""
        return _run(f"xdotool mousemove {x} {y}")

    @staticmethod
    def mouse_click(button: int = 1) -> dict:
        """Click mouse button (1=left, 2=middle, 3=right)."""
        return _run(f"xdotool click {button}")

    @staticmethod
    def mouse_click_at(x: int, y: int, button: int = 1) -> dict:
        """Move mouse to position and click."""
        return _run(f"xdotool mousemove {x} {y} click {button}")

    @staticmethod
    def mouse_double_click(button: int = 1) -> dict:
        """Double-click mouse button."""
        return _run(f"xdotool click --repeat 2 --delay 100 {button}")

    @staticmethod
    def mouse_scroll(direction: str = "down", clicks: int = 3) -> dict:
        """Scroll mouse wheel. direction: 'up' or 'down'."""
        btn = 4 if direction == "up" else 5
        return _run(f"xdotool click --repeat {clicks} --delay 50 {btn}")

    @staticmethod
    def clipboard_copy(text: str) -> dict:
        """Copy text to clipboard."""
        return _run(f"echo -n {shlex.quote(text)} | xclip -selection clipboard")

    @staticmethod
    def clipboard_paste() -> str:
        """Get clipboard contents."""
        r = _run("xclip -selection clipboard -o")
        return r["output"]

    @staticmethod
    def clipboard_paste_keystroke() -> dict:
        """Paste from clipboard via Ctrl+V."""
        return _run("xdotool key ctrl+v")


# ══════════════════════════════════════════════════════════════════════
# APP AGENT — launch, close, manage applications
# ══════════════════════════════════════════════════════════════════════

class AppAgent:
    """Launch, close, and manage desktop applications."""

    name = "app"

    @staticmethod
    def launch(app: str, args: str = "") -> dict:
        """Launch an application (backgrounded, won't block)."""
        cmd = f"DISPLAY={DISPLAY} {app} {args}"
        return _run(f"nohup {cmd} > /dev/null 2>&1 &")

    @staticmethod
    def close(window_title: str) -> dict:
        """Close a window by title (partial match)."""
        return _run(f"wmctrl -c {shlex.quote(window_title)}")

    @staticmethod
    def focus(window_title: str) -> dict:
        """Bring a window to front and focus it."""
        return _run(f"wmctrl -a {shlex.quote(window_title)}")

    @staticmethod
    def list_windows() -> list[dict]:
        """List all open windows."""
        r = _run("wmctrl -l")
        windows = []
        for line in r["output"].split("\n"):
            parts = line.split(None, 3)
            if len(parts) >= 4:
                windows.append({
                    "id": parts[0],
                    "desktop": parts[1],
                    "host": parts[2],
                    "title": parts[3],
                })
        return windows

    @staticmethod
    def move_window(title: str, x: int, y: int, w: int, h: int) -> dict:
        """Move and resize a window."""
        return _run(f"wmctrl -r {shlex.quote(title)} -e 0,{x},{y},{w},{h}")

    @staticmethod
    def minimize(title: str) -> dict:
        """Minimize a window."""
        return _run(f"xdotool search --name {shlex.quote(title)} windowminimize")

    @staticmethod
    def maximize(title: str) -> dict:
        """Maximize a window."""
        return _run(f"wmctrl -r {shlex.quote(title)} -b add,maximized_vert,maximized_horz")

    @staticmethod
    def is_running(process_name: str) -> bool:
        """Check if a process is running."""
        r = _run(f"pgrep -x {shlex.quote(process_name)}")
        return r["success"]

    # Common app shortcuts
    @staticmethod
    def firefox(url: str = "") -> dict:
        return AppAgent.launch("firefox", url)

    @staticmethod
    def terminal() -> dict:
        return AppAgent.launch("xfce4-terminal")

    @staticmethod
    def file_manager(path: str = HOME) -> dict:
        return AppAgent.launch("thunar", path)

    @staticmethod
    def text_editor(path: str = "") -> dict:
        return AppAgent.launch("mousepad", path)

    @staticmethod
    def burpsuite() -> dict:
        return AppAgent.launch("burpsuite")

    @staticmethod
    def wireshark() -> dict:
        return AppAgent.launch("wireshark")


# ══════════════════════════════════════════════════════════════════════
# SYSTEM AGENT — services, packages, users, processes
# ══════════════════════════════════════════════════════════════════════

class SystemAgent:
    """Manage system services, packages, processes, and users."""

    name = "system"

    # Services
    @staticmethod
    def service_start(name: str) -> dict:
        return _run(f"sudo systemctl start {name}")

    @staticmethod
    def service_stop(name: str) -> dict:
        return _run(f"sudo systemctl stop {name}")

    @staticmethod
    def service_restart(name: str) -> dict:
        return _run(f"sudo systemctl restart {name}")

    @staticmethod
    def service_status(name: str) -> dict:
        return _run(f"systemctl status {name}")

    @staticmethod
    def service_enable(name: str) -> dict:
        return _run(f"sudo systemctl enable {name}")

    # Packages
    @staticmethod
    def install(package: str) -> dict:
        return _run(f"sudo apt-get install -y {package}", timeout=300)

    @staticmethod
    def remove(package: str) -> dict:
        return _run(f"sudo apt-get remove -y {package}", timeout=120)

    @staticmethod
    def update() -> dict:
        return _run("sudo apt-get update", timeout=120)

    @staticmethod
    def upgrade() -> dict:
        return _run("sudo apt-get upgrade -y", timeout=600)

    # Processes
    @staticmethod
    def ps(filter: str = "") -> str:
        if filter:
            return TerminalAgent.get_output(f"ps aux | grep {shlex.quote(filter)} | grep -v grep")
        return TerminalAgent.get_output("ps aux --sort=-%mem | head -20")

    @staticmethod
    def kill(pid: int) -> dict:
        return _run(f"kill {pid}")

    @staticmethod
    def kill_name(name: str) -> dict:
        return _run(f"pkill {shlex.quote(name)}")

    # System info
    @staticmethod
    def info() -> dict:
        return {
            "hostname": TerminalAgent.get_output("hostname"),
            "kernel": TerminalAgent.get_output("uname -r"),
            "uptime": TerminalAgent.get_output("uptime -p"),
            "cpu": TerminalAgent.get_output("nproc"),
            "memory": TerminalAgent.get_output("free -h | head -2"),
            "disk": TerminalAgent.get_output("df -h / | tail -1"),
            "user": TerminalAgent.get_output("whoami"),
        }

    # Power
    @staticmethod
    def shutdown() -> dict:
        return _run("sudo shutdown -h now")

    @staticmethod
    def reboot() -> dict:
        return _run("sudo reboot")

    @staticmethod
    def suspend() -> dict:
        return _run("sudo systemctl suspend")

    @staticmethod
    def hibernate() -> dict:
        return _run("sudo systemctl hibernate")

    @staticmethod
    def hybrid_sleep() -> dict:
        return _run("sudo systemctl hybrid-sleep")

    @staticmethod
    def lock() -> dict:
        # Try common lock commands in order
        for cmd in ["loginctl lock-session", "xdg-screensaver lock",
                     f"DISPLAY={DISPLAY} xflock4", "gnome-screensaver-command -l"]:
            result = _run(cmd)
            if result.get("exit_code", 1) == 0:
                return result
        return {"exit_code": 1, "output": "No lock method available"}

    @staticmethod
    def scheduled_shutdown(minutes: int = 1) -> dict:
        return _run(f"sudo shutdown -h +{minutes}")

    @staticmethod
    def cancel_shutdown() -> dict:
        return _run("sudo shutdown -c")


# ══════════════════════════════════════════════════════════════════════
# NETWORK AGENT — connections, scanning, firewall
# ══════════════════════════════════════════════════════════════════════

class NetworkAgent:
    """Full network control — Ulrich's ByteLAN network."""

    name = "network"

    # ── Network Config ──
    WIFI_SSID = "ByteLAN"
    WIFI_PASS = "697968751ando"
    GATEWAY = "10.10.0.1"        # OpenWrt router
    SUBNET = "10.10.0.0/24"
    INTERFACE = "wlan0"

    # Known devices on ByteLAN
    DEVICES = {
        "router":      {"ip": "10.10.0.1",   "name": "OpenWrt", "mac": "30:de:4b:3d:0d:11"},
        "this_machine": {"ip": "10.10.0.121", "name": "Moon (Kali)"},
        "adguard":     {"ip": "10.10.0.113",  "name": "AdGuard DNS"},
        "cloudflared": {"ip": "10.10.0.125",  "name": "Cloudflared tunnel"},
        "nginx":       {"ip": "10.10.0.126",  "name": "Nginx Proxy Manager"},
        "docker":      {"ip": "10.10.0.129",  "name": "Docker host"},
        "pihole":      {"ip": "10.10.0.153",  "name": "Pi-hole DNS"},
        "phone":       {"ip": "10.10.0.171",  "name": "Ulrich S26 Ultra"},
        "wireguard":   {"ip": "10.10.0.197",  "name": "WireGuard VPN"},
        "yunohost":    {"ip": "10.10.0.206",  "name": "Yunohost"},
        "switch":      {"ip": "10.10.0.209",  "name": "Network Switch"},
        "rustdesk":    {"ip": "10.10.0.215",  "name": "RustDesk Server"},
        "wordpress":   {"ip": "10.10.0.222",  "name": "WordPress"},
        "heimdall":    {"ip": "10.10.0.234",  "name": "Heimdall Dashboard"},
        "vaultwarden": {"ip": "10.10.0.244",  "name": "Vaultwarden"},
    }

    # ── Basic ──

    @staticmethod
    def ip() -> str:
        return TerminalAgent.get_output("ip -4 addr show wlan0 | grep inet | awk '{print $2}' | cut -d/ -f1")

    @staticmethod
    def interfaces() -> str:
        return TerminalAgent.get_output("ip link show")

    @staticmethod
    def connections() -> str:
        return TerminalAgent.get_output("ss -tuln")

    @staticmethod
    def ping(host: str, count: int = 4) -> dict:
        return _run(f"ping -c {count} {shlex.quote(host)}", timeout=30)

    @staticmethod
    def dns_lookup(domain: str) -> str:
        return TerminalAgent.get_output(f"dig +short {shlex.quote(domain)}")

    @staticmethod
    def traceroute(host: str) -> dict:
        return _run(f"traceroute -m 15 {shlex.quote(host)}", timeout=60)

    @staticmethod
    def scan(target: str, options: str = "-sV") -> dict:
        """Nmap scan."""
        return _run(f"sudo nmap {options} {shlex.quote(target)}", timeout=300)

    @staticmethod
    def quick_scan(target: str) -> dict:
        """Fast nmap scan — top 100 ports."""
        return _run(f"sudo nmap -F {shlex.quote(target)}", timeout=60)

    @staticmethod
    def firewall_status() -> str:
        return TerminalAgent.get_output("sudo iptables -L -n --line-numbers")

    @staticmethod
    def wifi_scan() -> dict:
        return _run("sudo iwlist scan 2>/dev/null | grep -E 'ESSID|Quality|Encryption'")

    @staticmethod
    def download(url: str, output: str = "") -> dict:
        out = f"-O {shlex.quote(output)}" if output else ""
        return _run(f"wget -q {out} {shlex.quote(url)}", timeout=120)

    # ── ByteLAN Network Control ────────────────────────────────────

    @staticmethod
    def discover_devices() -> str:
        """Scan the entire LAN and list all devices."""
        return TerminalAgent.get_output(
            f"sudo nmap -sn {NetworkAgent.SUBNET} 2>/dev/null | grep -E 'report|MAC'")

    @staticmethod
    def device_info(target: str) -> str:
        """Full scan of a specific device (OS, services, ports)."""
        # Resolve friendly names
        for name, dev in NetworkAgent.DEVICES.items():
            if target.lower() in (name, dev.get("name", "").lower()):
                target = dev["ip"]
                break
        return TerminalAgent.get_output(
            f"sudo nmap -sV -O {shlex.quote(target)} 2>/dev/null")

    @staticmethod
    def list_known_devices() -> str:
        """List all known devices on ByteLAN."""
        lines = []
        for name, dev in NetworkAgent.DEVICES.items():
            mac = dev.get("mac", "")
            lines.append(f"  {dev['ip']:15s}  {dev['name']:25s}  {name:15s}  {mac}")
        return "\n".join(lines)

    @staticmethod
    def ping_all() -> str:
        """Ping all known devices — check which are online."""
        import concurrent.futures
        results = []

        def _ping_one(name, ip):
            r = _run(f"ping -c 1 -W 1 {ip}", timeout=3)
            status = "UP" if r["success"] else "DOWN"
            return f"  [{status:4s}] {ip:15s}  {name}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_ping_one, n, d["ip"]): n
                       for n, d in NetworkAgent.DEVICES.items()}
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        return "\n".join(sorted(results))

    @staticmethod
    def router_ssh(command: str) -> dict:
        """Execute a command on the OpenWrt router via SSH."""
        return _run(
            f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@{NetworkAgent.GATEWAY} {shlex.quote(command)}",
            timeout=15)

    @staticmethod
    def router_info() -> str:
        """Get router info via SSH or web API."""
        r = NetworkAgent.router_ssh("cat /etc/openwrt_release; uptime; free")
        if r["success"]:
            return r["output"]
        # Fallback: try curl to router web
        r2 = _run(f"curl -s http://{NetworkAgent.GATEWAY}/cgi-bin/luci/", timeout=5)
        return r2["output"][:500] if r2["success"] else "Router unreachable"

    @staticmethod
    def router_connected_clients() -> str:
        """List clients connected to the router."""
        r = NetworkAgent.router_ssh("cat /tmp/dhcp.leases 2>/dev/null || uci show dhcp")
        if r["success"]:
            return r["output"]
        return TerminalAgent.get_output(f"arp -a | grep -v incomplete")

    @staticmethod
    def router_restart_wifi() -> dict:
        """Restart WiFi on the router."""
        return NetworkAgent.router_ssh("wifi down && sleep 2 && wifi up")

    @staticmethod
    def router_block_device(mac: str) -> dict:
        """Block a device from the network by MAC address."""
        return NetworkAgent.router_ssh(
            f"uci add wireless mac-filter; uci set wireless.@mac-filter[-1].mac='{mac}'; "
            f"uci set wireless.@mac-filter[-1].action='deny'; uci commit wireless; wifi")

    @staticmethod
    def router_unblock_device(mac: str) -> dict:
        """Unblock a previously blocked device."""
        return NetworkAgent.router_ssh(
            f"uci show wireless | grep '{mac}' | head -1 | cut -d= -f1 | "
            f"xargs -I{{}} uci delete {{}}; uci commit wireless; wifi")

    @staticmethod
    def bandwidth_test() -> dict:
        """Test internet bandwidth."""
        return _run("speedtest-cli --simple 2>/dev/null || curl -s https://raw.githubusercontent.com/sivel/speedtest-cli/master/speedtest.py | python3", timeout=60)

    @staticmethod
    def monitor_traffic(duration: int = 10) -> dict:
        """Monitor network traffic for N seconds."""
        return _run(f"sudo timeout {duration} tcpdump -i {NetworkAgent.INTERFACE} -c 50 -nn 2>/dev/null | tail -20", timeout=duration + 5)

    @staticmethod
    def wifi_reconnect() -> dict:
        """Reconnect to ByteLAN WiFi."""
        return _run(
            f"nmcli device wifi connect {shlex.quote(NetworkAgent.WIFI_SSID)} "
            f"password {shlex.quote(NetworkAgent.WIFI_PASS)}")

    @staticmethod
    def wake_on_lan(mac: str) -> dict:
        """Send Wake-on-LAN packet to wake a device."""
        return _run(f"wakeonlan {shlex.quote(mac)} 2>/dev/null || etherwake {shlex.quote(mac)}")

    @staticmethod
    def port_forward(external_port: int, internal_ip: str, internal_port: int) -> dict:
        """Set up port forwarding on the router."""
        return NetworkAgent.router_ssh(
            f"uci add firewall redirect; "
            f"uci set firewall.@redirect[-1].src='wan'; "
            f"uci set firewall.@redirect[-1].dest='lan'; "
            f"uci set firewall.@redirect[-1].proto='tcp'; "
            f"uci set firewall.@redirect[-1].src_dport='{external_port}'; "
            f"uci set firewall.@redirect[-1].dest_ip='{internal_ip}'; "
            f"uci set firewall.@redirect[-1].dest_port='{internal_port}'; "
            f"uci commit firewall; /etc/init.d/firewall restart")

    @staticmethod
    def dns_entries() -> str:
        """List DNS entries (AdGuard/Pi-hole)."""
        # Try AdGuard API
        r = _run("curl -s http://10.10.0.113/control/filtering/status 2>/dev/null | head -20", timeout=5)
        if r["success"] and r["output"]:
            return r["output"]
        # Fallback: Pi-hole
        r = _run("curl -s http://10.10.0.153/admin/api.php?summary 2>/dev/null", timeout=5)
        return r["output"] if r["success"] else "DNS servers unreachable"

    @staticmethod
    def vpn_status() -> str:
        """Check WireGuard VPN status."""
        r = _run("sudo wg show 2>/dev/null")
        if r["success"] and r["output"]:
            return r["output"]
        return TerminalAgent.get_output("curl -s http://10.10.0.197:51820 2>/dev/null || echo 'WireGuard at 10.10.0.197'")


# ══════════════════════════════════════════════════════════════════════
# FILE AGENT — read, write, search, manage files
# ══════════════════════════════════════════════════════════════════════

class FileAgent:
    """Full file system access — read, write, edit, search any file on the system."""

    name = "file"

    @staticmethod
    def read(path: str, offset: int = 1, limit: int = 200) -> str:
        """Read a file with line numbers, or list a directory."""
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            # Try with sudo for protected files
            r = _run(f"sudo cat {shlex.quote(path)}")
            if r["success"]:
                return r["output"]
            return f"File not found: {path}"
        if os.path.isdir(path):
            return TerminalAgent.get_output(f"ls -la {shlex.quote(path)}")
        try:
            with open(path, "r", errors="replace") as f:
                lines = f.readlines()
            total = len(lines)
            start = max(0, offset - 1)
            end = min(total, start + limit)
            numbered = [f"{i + start + 1:4d} | {line.rstrip()}" for i, line in enumerate(lines[start:end])]
            result = "\n".join(numbered)
            if end < total:
                result += f"\n\n... ({total - end} more lines)"
            return result
        except PermissionError:
            # Retry with sudo
            return TerminalAgent.get_output(f"sudo cat {shlex.quote(path)}")
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def write(path: str, content: str) -> dict:
        """Write content to a file. Creates dirs if needed. Uses sudo if needed."""
        path = os.path.expanduser(path)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return {"success": True, "output": f"Wrote {len(content)} bytes to {path}"}
        except PermissionError:
            # Write via sudo
            r = _run(f"echo {shlex.quote(content)} | sudo tee {shlex.quote(path)} > /dev/null")
            return r
        except Exception as e:
            return {"success": False, "output": str(e)}

    @staticmethod
    def edit(path: str, old_string: str, new_string: str) -> dict:
        """Replace exact text in a file. old_string must be unique."""
        path = os.path.expanduser(path)
        try:
            with open(path, "r") as f:
                content = f.read()
            count = content.count(old_string)
            if count == 0:
                return {"success": False, "output": f"Text not found in {path}"}
            if count > 1:
                return {"success": False, "output": f"Text matches {count} locations — be more specific"}
            new_content = content.replace(old_string, new_string, 1)
            with open(path, "w") as f:
                f.write(new_content)
            return {"success": True, "output": f"Edited {path}"}
        except PermissionError:
            # Edit via sudo + sed
            escaped_old = old_string.replace("/", "\\/").replace("'", "'\\''")
            escaped_new = new_string.replace("/", "\\/").replace("'", "'\\''")
            return _run(f"sudo sed -i 's/{escaped_old}/{escaped_new}/' {shlex.quote(path)}")
        except Exception as e:
            return {"success": False, "output": str(e)}

    @staticmethod
    def append(path: str, content: str) -> dict:
        """Append content to a file."""
        path = os.path.expanduser(path)
        try:
            with open(path, "a") as f:
                f.write(content)
            return {"success": True, "output": f"Appended to {path}"}
        except PermissionError:
            return _run(f"echo {shlex.quote(content)} | sudo tee -a {shlex.quote(path)} > /dev/null")
        except Exception as e:
            return {"success": False, "output": str(e)}

    @staticmethod
    def search(pattern: str, path: str = "/") -> str:
        """Find files by name pattern anywhere on the system."""
        return TerminalAgent.get_output(
            f"find {shlex.quote(path)} -name {shlex.quote(pattern)} 2>/dev/null | head -30")

    @staticmethod
    def grep(pattern: str, path: str = ".", recursive: bool = True) -> str:
        """Search file contents for a pattern."""
        r = "-rn" if recursive else "-n"
        return TerminalAgent.get_output(
            f"grep {r} {shlex.quote(pattern)} {shlex.quote(path)} 2>/dev/null | head -30")

    @staticmethod
    def tree(path: str = ".", depth: int = 3) -> str:
        """Show directory tree."""
        return TerminalAgent.get_output(f"tree -L {depth} {shlex.quote(path)} 2>/dev/null || find {shlex.quote(path)} -maxdepth {depth} -print | head -50")

    @staticmethod
    def copy(src: str, dst: str) -> dict:
        return _run(f"cp -r {shlex.quote(src)} {shlex.quote(dst)}")

    @staticmethod
    def move(src: str, dst: str) -> dict:
        return _run(f"mv {shlex.quote(src)} {shlex.quote(dst)}")

    @staticmethod
    def delete(path: str) -> dict:
        return _run(f"rm -rf {shlex.quote(path)}")

    @staticmethod
    def mkdir(path: str) -> dict:
        return _run(f"mkdir -p {shlex.quote(path)}")

    @staticmethod
    def permissions(path: str, mode: str) -> dict:
        return _run(f"chmod {mode} {shlex.quote(path)}")

    @staticmethod
    def owner(path: str, user: str, group: str = "") -> dict:
        g = f":{group}" if group else ""
        return _run(f"sudo chown {user}{g} {shlex.quote(path)}")

    @staticmethod
    def size(path: str = ".") -> str:
        return TerminalAgent.get_output(f"du -sh {shlex.quote(path)}")

    @staticmethod
    def info(path: str) -> str:
        """Get detailed file info (stat, file type, permissions)."""
        return TerminalAgent.get_output(f"stat {shlex.quote(path)} && file {shlex.quote(path)}")

    @staticmethod
    def diff(file1: str, file2: str) -> str:
        """Compare two files."""
        return TerminalAgent.get_output(f"diff {shlex.quote(file1)} {shlex.quote(file2)}")

    @staticmethod
    def tail(path: str, lines: int = 20) -> str:
        """Read the last N lines of a file (good for logs)."""
        return TerminalAgent.get_output(f"tail -n {lines} {shlex.quote(path)}")

    @staticmethod
    def head(path: str, lines: int = 20) -> str:
        """Read the first N lines of a file."""
        return TerminalAgent.get_output(f"head -n {lines} {shlex.quote(path)}")


# ══════════════════════════════════════════════════════════════════════
# DESKTOP AGENT — windows, screenshots, volume, display, notifications
# ══════════════════════════════════════════════════════════════════════

class DesktopAgent:
    """Desktop environment control."""

    name = "desktop"

    @staticmethod
    def screenshot(path: str = "/tmp/screenshot.png") -> dict:
        return _run(f"DISPLAY={DISPLAY} scrot {shlex.quote(path)}")

    @staticmethod
    def screenshot_window(path: str = "/tmp/window.png") -> dict:
        return _run(f"DISPLAY={DISPLAY} scrot -u {shlex.quote(path)}")

    @staticmethod
    def notify(title: str, message: str) -> dict:
        return _run(f"DISPLAY={DISPLAY} notify-send {shlex.quote(title)} {shlex.quote(message)}")

    @staticmethod
    def volume_set(percent: int) -> dict:
        return _run(f"amixer set Master {percent}%")

    @staticmethod
    def volume_get() -> str:
        return TerminalAgent.get_output("amixer get Master | grep -oP '\\[\\d+%\\]' | head -1")

    @staticmethod
    def volume_mute() -> dict:
        return _run("amixer set Master mute")

    @staticmethod
    def volume_unmute() -> dict:
        return _run("amixer set Master unmute")

    @staticmethod
    def brightness_set(percent: int) -> dict:
        return _run(f"xrandr --output $(xrandr | grep ' connected' | head -1 | cut -d' ' -f1) --brightness {percent/100:.2f}")

    @staticmethod
    def resolution() -> str:
        return TerminalAgent.get_output("xrandr | grep '*'")

    @staticmethod
    def lock_screen() -> dict:
        return _run(f"DISPLAY={DISPLAY} xflock4")

    @staticmethod
    def wallpaper(path: str) -> dict:
        return _run(f"DISPLAY={DISPLAY} xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitor0/workspace0/last-image -s {shlex.quote(path)}")


# ══════════════════════════════════════════════════════════════════════
# SECURITY AGENT — wifi, pentesting, exploitation
# ══════════════════════════════════════════════════════════════════════

class SecurityAgent:
    """Offensive security and pentesting tools. All authorized by machine owner."""

    name = "security"

    # WiFi
    @staticmethod
    def wifi_interfaces() -> str:
        return TerminalAgent.get_output("sudo airmon-ng")

    @staticmethod
    def wifi_monitor_start(interface: str = "wlan0") -> dict:
        return _run(f"sudo airmon-ng start {interface}")

    @staticmethod
    def wifi_monitor_stop(interface: str = "wlan0mon") -> dict:
        return _run(f"sudo airmon-ng stop {interface}")

    @staticmethod
    def wifi_scan_airodump(interface: str = "wlan0mon", duration: int = 15) -> dict:
        return _run(f"sudo timeout {duration} airodump-ng {interface} 2>&1 || true", timeout=duration + 5)

    @staticmethod
    def wifi_deauth(bssid: str, interface: str = "wlan0mon", count: int = 10) -> dict:
        return _run(f"sudo aireplay-ng --deauth {count} -a {shlex.quote(bssid)} {interface}", timeout=30)

    @staticmethod
    def wifi_capture(bssid: str, channel: int, interface: str = "wlan0mon",
                     output: str = "/tmp/capture") -> dict:
        return _run(f"sudo timeout 30 airodump-ng -c {channel} --bssid {shlex.quote(bssid)} -w {output} {interface} 2>&1 || true", timeout=35)

    @staticmethod
    def wifi_crack(capture_file: str, wordlist: str = "/usr/share/wordlists/rockyou.txt") -> dict:
        return _run(f"sudo aircrack-ng -w {shlex.quote(wordlist)} {shlex.quote(capture_file)}", timeout=600)

    @staticmethod
    def wifite(args: str = "") -> dict:
        return _run(f"sudo timeout 120 wifite {args} 2>&1 || true", timeout=130)

    # Scanning & Recon
    @staticmethod
    def nmap(target: str, options: str = "-sV -sC") -> dict:
        return _run(f"sudo nmap {options} {shlex.quote(target)}", timeout=300)

    @staticmethod
    def nikto(target: str) -> dict:
        return _run(f"nikto -h {shlex.quote(target)}", timeout=300)

    @staticmethod
    def gobuster(target: str, wordlist: str = "/usr/share/wordlists/dirb/common.txt") -> dict:
        return _run(f"gobuster dir -u {shlex.quote(target)} -w {wordlist} -q", timeout=300)

    @staticmethod
    def sqlmap(target: str, options: str = "--batch") -> dict:
        return _run(f"sqlmap -u {shlex.quote(target)} {options}", timeout=300)

    @staticmethod
    def hydra(target: str, service: str, user: str, wordlist: str = "/usr/share/wordlists/rockyou.txt") -> dict:
        return _run(f"hydra -l {shlex.quote(user)} -P {shlex.quote(wordlist)} {shlex.quote(target)} {service}", timeout=300)

    # Exploitation
    @staticmethod
    def msfconsole(command: str) -> dict:
        return _run(f'msfconsole -q -x "{command}; exit"', timeout=120)

    @staticmethod
    def msfvenom(payload: str, lhost: str, lport: int, format: str = "elf",
                 output: str = "/tmp/payload") -> dict:
        return _run(f"msfvenom -p {payload} LHOST={lhost} LPORT={lport} -f {format} -o {shlex.quote(output)}")

    # Password
    @staticmethod
    def john(hash_file: str, wordlist: str = "/usr/share/wordlists/rockyou.txt") -> dict:
        return _run(f"john --wordlist={shlex.quote(wordlist)} {shlex.quote(hash_file)}", timeout=600)

    @staticmethod
    def hashcat(hash_file: str, mode: int = 0,
                wordlist: str = "/usr/share/wordlists/rockyou.txt") -> dict:
        return _run(f"hashcat -m {mode} {shlex.quote(hash_file)} {shlex.quote(wordlist)} --force", timeout=600)

    # OSINT
    @staticmethod
    def whois(domain: str) -> str:
        return TerminalAgent.get_output(f"whois {shlex.quote(domain)}")

    @staticmethod
    def theHarvester(domain: str) -> dict:
        return _run(f"theHarvester -d {shlex.quote(domain)} -b all -l 100", timeout=120)


# ══════════════════════════════════════════════════════════════════════
# VISION AGENT — camera, webcam, screen capture
# ══════════════════════════════════════════════════════════════════════

class VisionAgent:
    """Camera and visual perception."""

    name = "vision"

    @staticmethod
    def capture_photo(path: str = "/tmp/camera.jpg") -> dict:
        """Capture a photo from the webcam."""
        # Try fswebcam first, then ffmpeg
        r = _run(f"fswebcam -r 1280x720 --no-banner {shlex.quote(path)} 2>/dev/null")
        if not r["success"]:
            r = _run(f"ffmpeg -y -f v4l2 -video_size 1280x720 -i /dev/video0 -frames:v 1 {shlex.quote(path)} 2>/dev/null")
        return r

    @staticmethod
    def capture_video(path: str = "/tmp/camera.mp4", duration: int = 5) -> dict:
        """Record video from webcam."""
        return _run(f"ffmpeg -y -f v4l2 -video_size 1280x720 -i /dev/video0 -t {duration} {shlex.quote(path)} 2>/dev/null", timeout=duration + 10)

    @staticmethod
    def list_cameras() -> str:
        """List available video devices."""
        return TerminalAgent.get_output("ls -la /dev/video* 2>/dev/null; v4l2-ctl --list-devices 2>/dev/null || echo 'No cameras found'")

    @staticmethod
    def is_camera_available() -> bool:
        """Check if a camera is available."""
        r = _run("test -e /dev/video0")
        return r["success"]

    @staticmethod
    def screenshot(path: str = "/tmp/screenshot.png") -> dict:
        """Capture the screen."""
        return _run(f"DISPLAY={DISPLAY} scrot {shlex.quote(path)}")

    @staticmethod
    def screenshot_region(path: str = "/tmp/region.png") -> dict:
        """Capture a selected region (interactive)."""
        return _run(f"DISPLAY={DISPLAY} scrot -s {shlex.quote(path)}")

    @staticmethod
    def screen_record(path: str = "/tmp/screen.mp4", duration: int = 10) -> dict:
        """Record the screen."""
        return _run(
            f"ffmpeg -y -f x11grab -video_size 1920x1080 -i {DISPLAY} "
            f"-t {duration} -c:v libx264 -preset ultrafast {shlex.quote(path)} 2>/dev/null",
            timeout=duration + 10,
        )


# ══════════════════════════════════════════════════════════════════════
# SELF-REPAIR AGENT — diagnose and fix JARVIS itself
# ══════════════════════════════════════════════════════════════════════

class SelfRepairAgent:
    """JARVIS self-diagnosis and repair."""

    name = "self_repair"

    JARVIS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    @staticmethod
    def health_check() -> dict:
        """Full system health check."""
        checks = {}

        # Ollama
        r = _run("curl -s http://localhost:11434/api/tags", timeout=5)
        checks["ollama"] = "running" if r["success"] else "DOWN"

        # Web server
        r = _run("curl -s http://localhost:8765/api/mesh/ping", timeout=5)
        checks["web_server"] = "running" if r["success"] else "DOWN"

        # Models available
        if checks["ollama"] == "running":
            import json
            try:
                models = json.loads(r["output"]).get("models", []) if "models" in r["output"] else []
                checks["models"] = [m.get("name", "") for m in models]
            except Exception:
                checks["models"] = "parse error"

        # Disk space
        checks["disk"] = TerminalAgent.get_output("df -h / | tail -1")

        # Memory
        checks["memory"] = TerminalAgent.get_output("free -h | head -2")

        # Python deps
        r = _run("python3 -c 'import edge_tts, aiohttp, groq; print(\"OK\")'")
        checks["python_deps"] = "OK" if r["success"] else r["output"]

        # Audio
        r = _run("aplay -l 2>/dev/null | head -3")
        checks["audio"] = "OK" if r["success"] else "no audio devices"

        # Mic
        r = _run("arecord -l 2>/dev/null | head -3")
        checks["microphone"] = "OK" if r["success"] else "no mic"

        return checks

    @classmethod
    def restart_server(cls) -> dict:
        """Restart the JARVIS web server."""
        _run("pkill -f 'shells.web.server'")
        import time
        time.sleep(2)
        r = _run(
            f"cd {cls.JARVIS_ROOT} && PYTHONUNBUFFERED=1 python3 -m shells.web.server > /tmp/jarvis-web.log 2>&1 &"
        )
        time.sleep(5)
        check = _run("curl -s http://localhost:8765/api/mesh/ping", timeout=5)
        return {"success": check["success"], "output": "Server restarted" if check["success"] else "Restart failed"}

    @classmethod
    def restart_ollama(cls) -> dict:
        """Restart Ollama service."""
        _run("sudo systemctl restart ollama")
        import time
        time.sleep(3)
        r = _run("curl -s http://localhost:11434/api/tags", timeout=5)
        return {"success": r["success"], "output": "Ollama restarted" if r["success"] else "Ollama restart failed"}

    @classmethod
    def fix_audio(cls) -> dict:
        """Try to fix audio issues."""
        steps = [
            "pulseaudio --kill 2>/dev/null; pulseaudio --start 2>/dev/null || true",
            "amixer set Master unmute",
            "amixer set Master 70%",
        ]
        results = []
        for cmd in steps:
            r = _run(cmd)
            results.append(f"$ {cmd} → {'OK' if r['success'] else 'FAIL'}")
        return {"success": True, "output": "\n".join(results)}

    @classmethod
    def fix_mic(cls) -> dict:
        """Try to fix microphone issues."""
        steps = [
            "amixer set Capture unmute",
            "amixer set Capture 100%",
        ]
        results = []
        for cmd in steps:
            r = _run(cmd)
            results.append(f"$ {cmd} → {'OK' if r['success'] else 'FAIL'}")
        return {"success": True, "output": "\n".join(results)}

    @classmethod
    def fix_display(cls) -> dict:
        """Try to fix display/GUI issues."""
        steps = [
            f"export DISPLAY={DISPLAY}",
            f"xdpyinfo -display {DISPLAY} > /dev/null 2>&1 && echo 'Display OK' || echo 'Display FAIL'",
        ]
        results = []
        for cmd in steps:
            r = _run(cmd)
            results.append(r["output"])
        return {"success": True, "output": "\n".join(results)}

    @classmethod
    def install_deps(cls) -> dict:
        """Install/update all JARVIS Python dependencies."""
        r = _run(f"cd {cls.JARVIS_ROOT} && pip install -e . 2>&1 | tail -5", timeout=120)
        return r

    @classmethod
    def check_logs(cls, lines: int = 30) -> str:
        """Get recent JARVIS server logs."""
        return TerminalAgent.get_output(f"tail -n {lines} /tmp/jarvis-web.log")

    @classmethod
    def clear_cache(cls) -> dict:
        """Clear Python cache and restart clean."""
        r = _run(f"find {cls.JARVIS_ROOT} -type d -name __pycache__ -exec rm -rf {{}} + 2>/dev/null; echo 'Cache cleared'")
        return r

    @classmethod
    def self_update(cls) -> dict:
        """Pull latest code, reload, and report what changed."""
        # Get current commit
        before = _run(f"cd {cls.JARVIS_ROOT} && git rev-parse --short HEAD 2>/dev/null")
        before_hash = before["output"].strip() if before["success"] else "unknown"

        # Pull
        r = _run(f"cd {cls.JARVIS_ROOT} && git pull 2>&1", timeout=30)
        if not r["success"]:
            return r

        # Get new commit
        after = _run(f"cd {cls.JARVIS_ROOT} && git rev-parse --short HEAD 2>/dev/null")
        after_hash = after["output"].strip() if after["success"] else "unknown"

        if before_hash == after_hash:
            return {"success": True, "output": "Already up to date. No changes.",
                    "changelog": "No new updates."}

        # Get changelog
        changelog = _run(
            f"cd {cls.JARVIS_ROOT} && git log --oneline {before_hash}..{after_hash} 2>/dev/null")

        # Hot reload
        reload_r = _run("curl -s -X POST http://localhost:8765/api/reload", timeout=30)

        return {
            "success": True,
            "output": f"Updated from {before_hash} to {after_hash}",
            "changelog": changelog.get("output", ""),
            "reloaded": reload_r.get("success", False),
        }

    @classmethod
    def auto_update_check(cls) -> dict:
        """Check if updates are available without applying them."""
        r = _run(f"cd {cls.JARVIS_ROOT} && git fetch 2>/dev/null && "
                 f"git log HEAD..origin/main --oneline 2>/dev/null", timeout=15)
        if r["success"] and r["output"].strip():
            count = len(r["output"].strip().split("\n"))
            return {"has_updates": True, "count": count, "changes": r["output"]}
        return {"has_updates": False, "count": 0, "changes": ""}


# ══════════════════════════════════════════════════════════════════════
# AGENT REGISTRY — all agents in one place
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# CODE AGENT — generate scripts, commands, code on the fly
# ══════════════════════════════════════════════════════════════════════

class CodeAgent:
    """Generate and execute scripts, commands, and code."""

    name = "code"

    @staticmethod
    def generate_and_run(code: str, language: str = "bash") -> dict:
        """Write code to a temp file and execute it."""
        import tempfile
        ext_map = {"bash": ".sh", "python": ".py", "python3": ".py",
                   "node": ".js", "ruby": ".rb", "perl": ".pl"}
        runner_map = {"bash": "bash", "python": "python3", "python3": "python3",
                      "node": "node", "ruby": "ruby", "perl": "perl"}
        ext = ext_map.get(language, ".sh")
        runner = runner_map.get(language, "bash")

        with tempfile.NamedTemporaryFile(mode="w", suffix=ext, delete=False, prefix="jarvis_") as f:
            f.write(code)
            path = f.name
        os.chmod(path, 0o755)
        r = _run(f"{runner} {path}", timeout=120)
        os.unlink(path)
        return r

    @staticmethod
    def run_python(code: str) -> dict:
        """Execute Python code directly."""
        return CodeAgent.generate_and_run(code, "python3")

    @staticmethod
    def run_bash(script: str) -> dict:
        """Execute a bash script."""
        return CodeAgent.generate_and_run(script, "bash")

    @staticmethod
    def create_file(path: str, content: str) -> dict:
        """Create a file with generated code."""
        path = os.path.expanduser(path)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            os.chmod(path, 0o755)
            return {"success": True, "output": f"Created {path}"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    @staticmethod
    def explain_command(cmd: str) -> str:
        """Use 'man' or '--help' to explain a command."""
        r = _run(f"{cmd} --help 2>&1 | head -30", timeout=5)
        if r["success"] and r["output"]:
            return r["output"]
        r = _run(f"man {shlex.quote(cmd)} 2>/dev/null | head -40", timeout=5)
        return r["output"] if r["output"] else f"No help found for {cmd}"


# ══════════════════════════════════════════════════════════════════════
# RESEARCH AGENT — web search, fetch pages, gather info
# ══════════════════════════════════════════════════════════════════════

class ResearchAgent:
    """Search the web, fetch pages, gather information."""

    name = "research"

    @staticmethod
    def search(query: str, max_results: int = 5) -> list[dict]:
        """Search the web via DuckDuckGo."""
        try:
            from brain.internet.search import web_search
            return web_search(query, max_results)
        except Exception as e:
            return [{"title": "Error", "url": "", "body": str(e)}]

    @staticmethod
    def fetch(url: str) -> str:
        """Fetch and extract text from a URL."""
        try:
            from brain.internet.scraper import fetch_page
            content = fetch_page(url)
            if content and len(content) > 5000:
                content = content[:5000] + "\n... (truncated)"
            return content or "No content extracted."
        except Exception as e:
            return f"Fetch error: {e}"

    @staticmethod
    def search_and_summarize(query: str) -> str:
        """Search and return formatted results."""
        results = ResearchAgent.search(query)
        if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"{r.get('title', '?')}")
            lines.append(f"  {r.get('url', '')}")
            lines.append(f"  {r.get('body', '')[:300]}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def quick_answer(query: str) -> str:
        """Search and return just the top snippet."""
        results = ResearchAgent.search(query, max_results=3)
        if results:
            return results[0].get("body", "No answer found.")[:500]
        return "No results found."

    @staticmethod
    def fetch_multiple(urls: list[str]) -> dict[str, str]:
        """Fetch multiple URLs and return their content."""
        content = {}
        for url in urls[:5]:
            content[url] = ResearchAgent.fetch(url)
        return content


# ══════════════════════════════════════════════════════════════════════
# DEEP RESEARCH AGENT — research + implement
# ══════════════════════════════════════════════════════════════════════

class DeepResearchAgent:
    """Research a topic, then implement the solution."""

    name = "deep_research"

    @staticmethod
    def research_and_implement(query: str, llm_query_fn=None) -> dict:
        """Full pipeline: search → read → understand → implement.

        1. Search the web for the topic
        2. Fetch top results for detailed info
        3. Extract the key steps/commands
        4. Return structured research ready for implementation

        The brain adapter calls the LLM with this research context
        to generate the actual implementation commands.
        """
        # Step 1: Search
        results = ResearchAgent.search(query, max_results=5)
        if not results:
            return {"success": False, "research": "", "summary": "No results found."}

        # Step 2: Fetch top 2 results for detail
        pages = []
        for r in results[:2]:
            url = r.get("url", "")
            if url:
                content = ResearchAgent.fetch(url)
                if content and len(content) > 100:
                    pages.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "content": content[:3000],
                    })

        # Step 3: Build research context
        research = f"Query: {query}\n\n"
        research += "=== SEARCH RESULTS ===\n"
        for r in results:
            research += f"- {r.get('title', '')}: {r.get('body', '')[:200]}\n"
        research += "\n"

        if pages:
            research += "=== DETAILED PAGES ===\n"
            for p in pages:
                research += f"\n--- {p['title']} ({p['url']}) ---\n"
                research += p["content"][:2000] + "\n"

        return {
            "success": True,
            "research": research,
            "result_count": len(results),
            "pages_fetched": len(pages),
            "summary": f"Found {len(results)} results, read {len(pages)} pages in detail.",
        }

    @staticmethod
    def how_to(query: str) -> dict:
        """Research 'how to' do something — returns steps."""
        return DeepResearchAgent.research_and_implement(f"how to {query} linux kali")

    @staticmethod
    def find_tool(description: str) -> dict:
        """Find the right tool/command for a task."""
        return DeepResearchAgent.research_and_implement(
            f"best linux command tool for {description}")

    @staticmethod
    def find_command(description: str) -> str:
        """Search for the right command syntax."""
        results = ResearchAgent.search(
            f"{description} linux command example", max_results=3)
        if results:
            snippets = [r.get("body", "")[:300] for r in results]
            return "\n\n".join(snippets)
        return "No results found."


# ══════════════════════════════════════════════════════════════════════
# ORCHESTRATOR AGENT — runs multiple agents/commands for complex tasks
# ══════════════════════════════════════════════════════════════════════

class OrchestratorAgent:
    """Break complex tasks into steps, run multiple agents, collect results.

    Given a task, the orchestrator:
    1. Analyzes what needs to be done
    2. Maps steps to the right agents/commands
    3. Runs them in sequence or parallel
    4. Collects and returns combined output

    This is the "brain" agent — it coordinates all other agents.
    """

    name = "orchestrator"

    # Task templates — common multi-step operations
    PLAYBOOKS = {
        "recon": {
            "description": "Full network reconnaissance",
            "steps": [
                ("network", "ip", {}, "Get our IP"),
                ("bash", "sudo nmap -sn {target}/24 2>/dev/null | grep 'Nmap scan report' | head -20", {}, "Discover hosts"),
                ("bash", "sudo nmap -sV -F {target} 2>/dev/null", {}, "Service scan"),
            ],
        },
        "system_audit": {
            "description": "Full system audit",
            "steps": [
                ("bash", "uname -a", {}, "Kernel info"),
                ("bash", "uptime", {}, "Uptime"),
                ("bash", "free -h | head -2", {}, "Memory"),
                ("bash", "df -h / | tail -1", {}, "Disk"),
                ("bash", "nproc", {}, "CPU cores"),
                ("bash", "who", {}, "Logged in users"),
                ("bash", "ss -tuln | head -15", {}, "Open ports"),
                ("bash", "sudo systemctl list-units --type=service --state=running | head -15", {}, "Running services"),
                ("bash", "ps aux --sort=-%mem | head -8", {}, "Top processes"),
            ],
        },
        "security_audit": {
            "description": "Quick security audit",
            "steps": [
                ("bash", "sudo cat /etc/shadow | wc -l", {}, "User accounts"),
                ("bash", "sudo find / -perm -4000 -type f 2>/dev/null | head -10", {}, "SUID binaries"),
                ("bash", "ss -tuln", {}, "Listening ports"),
                ("bash", "sudo iptables -L -n 2>/dev/null | head -15", {}, "Firewall rules"),
                ("bash", "cat /etc/ssh/sshd_config 2>/dev/null | grep -v '^#' | grep -v '^$' | head -10", {}, "SSH config"),
                ("bash", "sudo lastlog | head -10", {}, "Last logins"),
            ],
        },
        "cleanup": {
            "description": "System cleanup",
            "steps": [
                ("bash", "sudo apt autoremove -y 2>&1 | tail -3", {}, "Remove unused packages"),
                ("bash", "sudo apt clean 2>&1", {}, "Clear apt cache"),
                ("bash", "sudo journalctl --vacuum-time=3d 2>&1 | tail -1", {}, "Trim logs"),
                ("bash", "find /tmp -type f -mtime +7 -delete 2>/dev/null; echo 'Cleaned /tmp'", {}, "Clean temp files"),
                ("bash", "df -h / | tail -1", {}, "Disk after cleanup"),
            ],
        },
        "wifi_attack": {
            "description": "WiFi reconnaissance and attack prep",
            "steps": [
                ("bash", "sudo airmon-ng 2>/dev/null", {}, "List wireless interfaces"),
                ("bash", "sudo iwlist scan 2>/dev/null | grep -E 'ESSID|Quality|Encryption' | head -30", {}, "Scan nearby networks"),
            ],
        },
        "web_recon": {
            "description": "Web application reconnaissance",
            "steps": [
                ("bash", "dig +short {target} 2>/dev/null", {}, "DNS lookup"),
                ("bash", "curl -sI {target} 2>/dev/null | head -15", {}, "HTTP headers"),
                ("bash", "whois {target} 2>/dev/null | head -20", {}, "WHOIS info"),
                ("bash", "sudo nmap -sV -p 80,443,8080,8443 {target} 2>/dev/null", {}, "Web ports scan"),
            ],
        },
    }

    @staticmethod
    def run_playbook(name: str, variables: dict = None) -> dict:
        """Run a predefined playbook — a sequence of agent tasks."""
        if name not in OrchestratorAgent.PLAYBOOKS:
            return {
                "success": False,
                "output": f"Unknown playbook: {name}. Available: {', '.join(OrchestratorAgent.PLAYBOOKS.keys())}",
            }

        playbook = OrchestratorAgent.PLAYBOOKS[name]
        variables = variables or {}
        results = []
        all_success = True

        for agent_type, cmd_or_method, args, description in playbook["steps"]:
            # Substitute variables in commands
            if isinstance(cmd_or_method, str):
                for k, v in variables.items():
                    cmd_or_method = cmd_or_method.replace(f"{{{k}}}", v)

            if agent_type == "bash":
                r = _run(cmd_or_method, timeout=120)
                results.append({
                    "step": description,
                    "command": cmd_or_method,
                    "output": r["output"][:500],
                    "success": r["success"],
                })
                if not r["success"]:
                    all_success = False
            elif agent_type == "network":
                method = getattr(NetworkAgent, cmd_or_method, None)
                if method:
                    output = method(**args) if args else method()
                    results.append({
                        "step": description,
                        "output": str(output)[:500],
                        "success": True,
                    })

        return {
            "success": all_success,
            "playbook": name,
            "description": playbook["description"],
            "steps_run": len(results),
            "results": results,
            "output": OrchestratorAgent._format_results(results),
        }

    @staticmethod
    def run_commands(commands: list[str], parallel: bool = False) -> dict:
        """Run multiple bash commands, sequentially or in parallel."""
        import concurrent.futures

        if parallel:
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(_run, cmd, 60): cmd for cmd in commands}
                for future in concurrent.futures.as_completed(futures):
                    cmd = futures[future]
                    try:
                        r = future.result()
                        results.append({"command": cmd, "output": r["output"][:500],
                                        "success": r["success"]})
                    except Exception as e:
                        results.append({"command": cmd, "output": str(e), "success": False})
        else:
            results = []
            for cmd in commands:
                r = _run(cmd.strip(), timeout=60)
                results.append({"command": cmd, "output": r["output"][:500],
                                "success": r["success"]})
                # Stop on failure in sequential mode
                if not r["success"]:
                    break

        return {
            "success": all(r["success"] for r in results),
            "commands_run": len(results),
            "results": results,
            "output": OrchestratorAgent._format_results(results),
        }

    @staticmethod
    def run_pipeline(steps: list[dict]) -> dict:
        """Run a pipeline where each step can use the previous step's output.

        Each step: {"cmd": "...", "use_output": True/False, "description": "..."}
        If use_output is True, {prev_output} in the command gets replaced.
        """
        results = []
        prev_output = ""

        for step in steps:
            cmd = step.get("cmd", "")
            desc = step.get("description", cmd[:40])

            # Inject previous output
            if step.get("use_output") and prev_output:
                cmd = cmd.replace("{prev_output}", prev_output.strip())

            r = _run(cmd, timeout=120)
            prev_output = r["output"]
            results.append({
                "step": desc,
                "command": cmd,
                "output": r["output"][:500],
                "success": r["success"],
            })

            if not r["success"] and not step.get("continue_on_fail"):
                break

        return {
            "success": all(r["success"] for r in results),
            "steps_run": len(results),
            "results": results,
            "output": OrchestratorAgent._format_results(results),
        }

    @staticmethod
    def multi_agent(tasks: list[dict]) -> dict:
        """Run tasks across different agents in parallel.

        Each task: {"agent": "network", "method": "ip"} or {"bash": "ls -la"}
        """
        import concurrent.futures
        results = []

        def _run_task(task):
            if "bash" in task:
                return {"task": task["bash"][:40], **_run(task["bash"], 60)}
            agent_name = task.get("agent")
            method_name = task.get("method")
            args = task.get("args", {})
            agent_cls = AGENTS.get(agent_name)
            if agent_cls and hasattr(agent_cls, method_name):
                method = getattr(agent_cls, method_name)
                result = method(**args) if args else method()
                if isinstance(result, dict):
                    return {"task": f"{agent_name}.{method_name}", **result}
                return {"task": f"{agent_name}.{method_name}",
                        "output": str(result)[:500], "success": True}
            return {"task": f"{agent_name}.{method_name}",
                    "output": "Agent/method not found", "success": False}

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            future_map = {pool.submit(_run_task, t): t for t in tasks}
            for future in concurrent.futures.as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"task": "?", "output": str(e), "success": False})

        return {
            "success": all(r.get("success", False) for r in results),
            "tasks_run": len(results),
            "results": results,
            "output": OrchestratorAgent._format_results(results),
        }

    @staticmethod
    def _format_results(results: list[dict]) -> str:
        """Format results for display/summarization."""
        lines = []
        for r in results:
            label = r.get("step") or r.get("task") or r.get("command", "?")[:40]
            status = "OK" if r.get("success") else "FAIL"
            output = r.get("output", "")[:200]
            lines.append(f"[{status}] {label}\n{output}")
        return "\n\n".join(lines)

    @staticmethod
    def list_playbooks() -> list[str]:
        return list(OrchestratorAgent.PLAYBOOKS.keys())

    # ── Universal tool executor — like Claude Code ──────────────────

    @staticmethod
    def execute(action: dict) -> dict:
        """Execute any tool action. This is the universal entry point.

        Actions:
            {"bash": "ls -la"}
            {"read": "/etc/hosts"}
            {"read": "/etc/hosts", "offset": 10, "limit": 20}
            {"write": "/tmp/test.txt", "content": "hello"}
            {"edit": "/tmp/test.txt", "old": "hello", "new": "world"}
            {"grep": "pattern", "path": "/etc"}
            {"search": "*.py", "path": "/home"}
            {"todo": "add", "text": "Fix the bug"}
            {"todo": "list"}
            {"todo": "done", "id": 1}
            {"agent": "network", "method": "ip"}
        """
        try:
            # Bash
            if "bash" in action:
                return _run(action["bash"], action.get("timeout", 60))

            # Read file
            if "read" in action:
                path = action["read"]
                offset = action.get("offset", 1)
                limit = action.get("limit", 200)
                output = FileAgent.read(path, offset, limit)
                return {"success": True, "output": output}

            # Write file
            if "write" in action:
                return FileAgent.write(action["write"], action.get("content", ""))

            # Edit file
            if "edit" in action:
                return FileAgent.edit(
                    action["edit"],
                    action.get("old", ""),
                    action.get("new", ""),
                )

            # Append to file
            if "append" in action:
                return FileAgent.append(action["append"], action.get("content", ""))

            # Grep / search content
            if "grep" in action:
                output = FileAgent.grep(
                    action["grep"],
                    action.get("path", "."),
                    action.get("recursive", True),
                )
                return {"success": bool(output), "output": output}

            # Find / search files
            if "search" in action or "find" in action:
                pattern = action.get("search") or action.get("find", "")
                path = action.get("path", "/")
                output = FileAgent.search(pattern, path)
                return {"success": bool(output), "output": output}

            # Diff
            if "diff" in action:
                output = FileAgent.diff(action["diff"], action.get("other", ""))
                return {"success": True, "output": output}

            # Tail
            if "tail" in action:
                output = FileAgent.tail(action["tail"], action.get("lines", 20))
                return {"success": True, "output": output}

            # Tree
            if "tree" in action:
                output = FileAgent.tree(action["tree"], action.get("depth", 3))
                return {"success": True, "output": output}

            # Todo list (stored in SQLite)
            if "todo" in action:
                return OrchestratorAgent._handle_todo(action)

            # Call any agent method
            if "agent" in action:
                agent_name = action["agent"]
                method_name = action.get("method", "")
                args = action.get("args", {})
                agent_cls = AGENTS.get(agent_name)
                if agent_cls and hasattr(agent_cls, method_name):
                    method = getattr(agent_cls, method_name)
                    result = method(**args) if args else method()
                    if isinstance(result, dict):
                        return result
                    return {"success": True, "output": str(result)}
                return {"success": False, "output": f"Unknown: {agent_name}.{method_name}"}

            return {"success": False, "output": f"Unknown action: {action}"}

        except Exception as e:
            return {"success": False, "output": f"Error: {e}"}

    # ── Todo list ──────────────────────────────────────────────────

    _TODO_FILE = os.path.expanduser("~/.jarvis/todos.json")

    @staticmethod
    def _handle_todo(action: dict) -> dict:
        """Manage a persistent todo list."""
        import json

        # Load
        todos = []
        if os.path.exists(OrchestratorAgent._TODO_FILE):
            try:
                with open(OrchestratorAgent._TODO_FILE) as f:
                    todos = json.load(f)
            except Exception:
                todos = []

        op = action.get("todo", "list")

        if op == "list":
            if not todos:
                return {"success": True, "output": "No todos."}
            lines = []
            for i, t in enumerate(todos):
                status = "DONE" if t.get("done") else "TODO"
                lines.append(f"  [{i+1}] [{status}] {t['text']}")
            return {"success": True, "output": "\n".join(lines)}

        if op == "add":
            text = action.get("text", "")
            if text:
                todos.append({"text": text, "done": False, "created": time.time()})
                with open(OrchestratorAgent._TODO_FILE, "w") as f:
                    json.dump(todos, f)
                return {"success": True, "output": f"Added: {text}"}
            return {"success": False, "output": "No text provided."}

        if op == "done":
            idx = action.get("id", 0) - 1
            if 0 <= idx < len(todos):
                todos[idx]["done"] = True
                with open(OrchestratorAgent._TODO_FILE, "w") as f:
                    json.dump(todos, f)
                return {"success": True, "output": f"Marked done: {todos[idx]['text']}"}
            return {"success": False, "output": f"Invalid todo #{idx+1}"}

        if op == "remove" or op == "delete":
            idx = action.get("id", 0) - 1
            if 0 <= idx < len(todos):
                removed = todos.pop(idx)
                with open(OrchestratorAgent._TODO_FILE, "w") as f:
                    json.dump(todos, f)
                return {"success": True, "output": f"Removed: {removed['text']}"}
            return {"success": False, "output": f"Invalid todo #{idx+1}"}

        if op == "clear":
            todos = [t for t in todos if not t.get("done")]
            with open(OrchestratorAgent._TODO_FILE, "w") as f:
                json.dump(todos, f)
            return {"success": True, "output": "Cleared completed todos."}

        return {"success": False, "output": f"Unknown todo op: {op}"}

    # ── Run a sequence of mixed actions ────────────────────────────

    @staticmethod
    def run_actions(actions: list[dict]) -> dict:
        """Run a list of mixed actions (bash, read, write, grep, etc.)
        sequentially. Each action can reference {prev} for previous output.
        """
        results = []
        prev_output = ""

        for action in actions:
            # Substitute {prev} in string values
            resolved = {}
            for k, v in action.items():
                if isinstance(v, str):
                    resolved[k] = v.replace("{prev}", prev_output.strip())
                else:
                    resolved[k] = v

            r = OrchestratorAgent.execute(resolved)
            prev_output = r.get("output", "")
            results.append({
                "action": str(action)[:60],
                "output": r.get("output", "")[:500],
                "success": r.get("success", False),
            })

        return {
            "success": all(r["success"] for r in results),
            "steps_run": len(results),
            "results": results,
            "output": OrchestratorAgent._format_results(results),
        }


# ══════════════════════════════════════════════════════════════════════
# SERVER AGENT — Proxmox VMs, containers, storage, remote SSH
# ══════════════════════════════════════════════════════════════════════

class ServerAgent:
    """Control Proxmox server — VMs, containers, storage, remote commands."""

    name = "server"

    HOST = "10.10.0.50"
    USER = "root"
    PASS = "697968751ando"
    WEB = "https://10.10.0.50:8006"

    # Known infrastructure
    VMS = {
        100: "redhat",
        101: "Kalilinux",
        102: "windows",
        107: "netbootxyz",
        108: "netbooter",
    }
    CONTAINERS = {
        103: "nextcloud",
        104: "docker",
        105: "wordpress",
        106: "heimdall-dashboard",
        109: "pihole",
        111: "yunohost",
        112: "adguard",
        113: "rustdeskserver",
        114: "wireguard",
        116: "cloudflared",
        118: "vaultwarden",
        119: "nginxproxymanager",
    }

    @staticmethod
    def _ssh(cmd: str, timeout: int = 30) -> dict:
        """Run command on Proxmox via SSH."""
        return _run(
            f"sshpass -p {shlex.quote(ServerAgent.PASS)} "
            f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "
            f"{ServerAgent.USER}@{ServerAgent.HOST} {shlex.quote(cmd)}",
            timeout=timeout)

    @staticmethod
    def _api(endpoint: str, method: str = "GET", data: str = "") -> dict:
        """Call Proxmox API."""
        # Get ticket
        r = _run(
            f"curl -sk -d 'username=root@pam&password={ServerAgent.PASS}' "
            f"{ServerAgent.WEB}/api2/json/access/ticket", timeout=10)
        if not r["success"]:
            return r
        import json
        try:
            ticket_data = json.loads(r["output"])
            ticket = ticket_data["data"]["ticket"]
            csrf = ticket_data["data"]["CSRFPreventionToken"]
        except Exception:
            return {"success": False, "output": "Auth failed"}

        cookie = f"PVEAuthCookie={ticket}"
        if method == "GET":
            return _run(
                f"curl -sk -b '{cookie}' {ServerAgent.WEB}/api2/json/{endpoint}",
                timeout=15)
        else:
            return _run(
                f"curl -sk -X {method} -b '{cookie}' -H 'CSRFPreventionToken: {csrf}' "
                f"-d '{data}' {ServerAgent.WEB}/api2/json/{endpoint}",
                timeout=15)

    # ── Server Info ──

    @staticmethod
    def status() -> str:
        """Get Proxmox server status."""
        return ServerAgent._ssh(
            "echo \"CPU: $(nproc) cores\"; free -h | head -2; "
            "echo ''; df -h / | tail -1; echo ''; uptime").get("output", "")

    @staticmethod
    def resources() -> str:
        """Full resource overview."""
        return ServerAgent._ssh(
            "pveversion; echo ''; qm list 2>/dev/null; echo ''; "
            "pct list 2>/dev/null; echo ''; pvesm status 2>/dev/null"
        ).get("output", "")

    # ── VM Management ──

    @staticmethod
    def list_vms() -> str:
        return ServerAgent._ssh("qm list 2>/dev/null").get("output", "")

    @staticmethod
    def start_vm(vmid: int) -> dict:
        return ServerAgent._ssh(f"qm start {vmid}")

    @staticmethod
    def stop_vm(vmid: int) -> dict:
        return ServerAgent._ssh(f"qm stop {vmid}")

    @staticmethod
    def reboot_vm(vmid: int) -> dict:
        return ServerAgent._ssh(f"qm reboot {vmid}")

    @staticmethod
    def vm_status(vmid: int) -> str:
        return ServerAgent._ssh(f"qm status {vmid}").get("output", "")

    @staticmethod
    def create_vm(name: str, memory: int = 2048, disk: int = 32, cores: int = 2) -> dict:
        """Create a new VM."""
        # Find next available VMID
        r = ServerAgent._ssh("pvesh get /cluster/nextid")
        if not r["success"]:
            return r
        vmid = r["output"].strip()
        return ServerAgent._ssh(
            f"qm create {vmid} --name {shlex.quote(name)} --memory {memory} "
            f"--cores {cores} --net0 virtio,bridge=vmbr0 "
            f"--scsi0 local:{disk} --ostype l26")

    @staticmethod
    def delete_vm(vmid: int) -> dict:
        return ServerAgent._ssh(f"qm destroy {vmid} --purge")

    # ── Container Management ──

    @staticmethod
    def list_containers() -> str:
        return ServerAgent._ssh("pct list 2>/dev/null").get("output", "")

    @staticmethod
    def start_container(ctid: int) -> dict:
        return ServerAgent._ssh(f"pct start {ctid}")

    @staticmethod
    def stop_container(ctid: int) -> dict:
        return ServerAgent._ssh(f"pct stop {ctid}")

    @staticmethod
    def restart_container(ctid: int) -> dict:
        return ServerAgent._ssh(f"pct reboot {ctid}")

    @staticmethod
    def container_status(ctid: int) -> str:
        return ServerAgent._ssh(f"pct status {ctid}").get("output", "")

    @staticmethod
    def container_exec(ctid: int, cmd: str) -> dict:
        """Run a command inside a container."""
        return ServerAgent._ssh(f"pct exec {ctid} -- {cmd}", timeout=30)

    @staticmethod
    def container_shell(ctid: int) -> str:
        """Get container IP for direct SSH."""
        r = ServerAgent._ssh(f"pct exec {ctid} -- hostname -I")
        return r.get("output", "").strip()

    @staticmethod
    def create_container(name: str, template: str = "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst",
                         memory: int = 512, disk: int = 8, cores: int = 1) -> dict:
        r = ServerAgent._ssh("pvesh get /cluster/nextid")
        if not r["success"]:
            return r
        ctid = r["output"].strip()
        return ServerAgent._ssh(
            f"pct create {ctid} {template} --hostname {shlex.quote(name)} "
            f"--memory {memory} --cores {cores} --rootfs local:{disk} "
            f"--net0 name=eth0,bridge=vmbr0,ip=dhcp --start 1",
            timeout=60)

    @staticmethod
    def delete_container(ctid: int) -> dict:
        return ServerAgent._ssh(f"pct stop {ctid} 2>/dev/null; pct destroy {ctid} --purge")

    # ── Storage ──

    @staticmethod
    def storage_status() -> str:
        return ServerAgent._ssh("pvesm status 2>/dev/null").get("output", "")

    @staticmethod
    def storage_content(storage: str = "local") -> str:
        return ServerAgent._ssh(f"pvesm list {storage} 2>/dev/null | head -20").get("output", "")

    # ── Backups ──

    @staticmethod
    def backup_container(ctid: int, storage: str = "local") -> dict:
        return ServerAgent._ssh(
            f"vzdump {ctid} --storage {storage} --mode snapshot --compress zstd",
            timeout=600)

    @staticmethod
    def backup_vm(vmid: int, storage: str = "local") -> dict:
        return ServerAgent._ssh(
            f"vzdump {vmid} --storage {storage} --mode snapshot --compress zstd",
            timeout=600)

    @staticmethod
    def list_backups(storage: str = "local") -> str:
        return ServerAgent._ssh(
            f"pvesm list {storage} --content backup 2>/dev/null").get("output", "")

    # ── Remote Command ──

    @staticmethod
    def ssh_command(cmd: str) -> dict:
        """Run any command on the Proxmox host."""
        return ServerAgent._ssh(cmd)

    # ── Resolve name to ID ──

    @staticmethod
    def resolve_id(name: str) -> int | None:
        """Resolve a VM/container name to its ID."""
        name_lower = name.lower().strip()
        for vmid, n in {**ServerAgent.VMS, **ServerAgent.CONTAINERS}.items():
            if name_lower in (n.lower(), str(vmid)):
                return vmid
        return None


# ══════════════════════════════════════════════════════════════════════
# TRANSFER AGENT — download, upload, sync files
# ══════════════════════════════════════════════════════════════════════

class TransferAgent:
    """Download, upload, and sync files — web, YouTube, torrents, SCP, rsync."""

    name = "transfer"

    DOWNLOAD_DIR = os.path.expanduser("~/Downloads")

    # ── Download ──

    @staticmethod
    def download(url: str, output: str = "") -> dict:
        """Download a file from URL (auto-picks best tool)."""
        dest = output or TransferAgent.DOWNLOAD_DIR
        os.makedirs(dest if os.path.isdir(dest) else os.path.dirname(dest) or ".", exist_ok=True)
        if os.path.isdir(dest):
            return _run(f"wget -q --show-progress -P {shlex.quote(dest)} {shlex.quote(url)}", timeout=300)
        return _run(f"wget -q --show-progress -O {shlex.quote(dest)} {shlex.quote(url)}", timeout=300)

    @staticmethod
    def download_fast(url: str, output: str = "") -> dict:
        """Fast multi-threaded download with aria2."""
        dest = output or TransferAgent.DOWNLOAD_DIR
        return _run(f"aria2c -x 8 -d {shlex.quote(dest)} {shlex.quote(url)}", timeout=600)

    @staticmethod
    def download_video(url: str, output: str = "") -> dict:
        """Download YouTube/video from URL."""
        dest = output or TransferAgent.DOWNLOAD_DIR
        return _run(f"yt-dlp -o '{dest}/%(title)s.%(ext)s' {shlex.quote(url)}", timeout=600)

    @staticmethod
    def download_audio(url: str, output: str = "") -> dict:
        """Download audio only from YouTube/video URL."""
        dest = output or TransferAgent.DOWNLOAD_DIR
        return _run(
            f"yt-dlp -x --audio-format mp3 -o '{dest}/%(title)s.%(ext)s' {shlex.quote(url)}",
            timeout=600)

    @staticmethod
    def download_torrent(magnet_or_file: str, output: str = "") -> dict:
        """Download via torrent (aria2)."""
        dest = output or TransferAgent.DOWNLOAD_DIR
        if magnet_or_file.startswith("magnet:"):
            return _run(f"aria2c -d {shlex.quote(dest)} {shlex.quote(magnet_or_file)}", timeout=600)
        return _run(f"aria2c -d {shlex.quote(dest)} -T {shlex.quote(magnet_or_file)}", timeout=600)

    # ── Upload / Transfer ──

    @staticmethod
    def upload_scp(local_path: str, remote: str) -> dict:
        """Upload file via SCP. remote = user@host:/path"""
        return _run(f"scp -r {shlex.quote(local_path)} {shlex.quote(remote)}", timeout=300)

    @staticmethod
    def download_scp(remote: str, local_path: str = "") -> dict:
        """Download file via SCP. remote = user@host:/path"""
        dest = local_path or TransferAgent.DOWNLOAD_DIR
        return _run(f"scp -r {shlex.quote(remote)} {shlex.quote(dest)}", timeout=300)

    @staticmethod
    def sync(source: str, dest: str) -> dict:
        """Sync files/dirs with rsync."""
        return _run(f"rsync -avz --progress {shlex.quote(source)} {shlex.quote(dest)}", timeout=600)

    @staticmethod
    def upload_to_server(local_path: str, remote_path: str = "/tmp/") -> dict:
        """Upload file to Proxmox server."""
        return _run(
            f"scp -r {shlex.quote(local_path)} root@10.10.0.50:{shlex.quote(remote_path)}",
            timeout=300)

    @staticmethod
    def download_from_server(remote_path: str, local_path: str = "") -> dict:
        """Download file from Proxmox server."""
        dest = local_path or TransferAgent.DOWNLOAD_DIR
        return _run(
            f"scp -r root@10.10.0.50:{shlex.quote(remote_path)} {shlex.quote(dest)}",
            timeout=300)

    # ── Info ──

    @staticmethod
    def list_downloads() -> str:
        """List files in Downloads folder."""
        return TerminalAgent.get_output(f"ls -lhS {TransferAgent.DOWNLOAD_DIR} | head -20")

    @staticmethod
    def download_status() -> str:
        """Check if any downloads are running."""
        return TerminalAgent.get_output("ps aux | grep -E 'wget|aria2c|yt-dlp|curl.*-o' | grep -v grep")


AGENTS = {
    "terminal": TerminalAgent,
    "input": InputAgent,
    "app": AppAgent,
    "system": SystemAgent,
    "network": NetworkAgent,
    "file": FileAgent,
    "desktop": DesktopAgent,
    "security": SecurityAgent,
    "vision": VisionAgent,
    "self_repair": SelfRepairAgent,
    "code": CodeAgent,
    "research": ResearchAgent,
    "deep_research": DeepResearchAgent,
    "orchestrator": OrchestratorAgent,
    "server": ServerAgent,
    "transfer": TransferAgent,
}


def get_agent(name: str):
    """Get an agent by name."""
    return AGENTS.get(name)


def list_agents() -> list[str]:
    """List all available agents."""
    return list(AGENTS.keys())
