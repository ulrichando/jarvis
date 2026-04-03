"""JARVIS Agent Dispatcher — routes user requests to the right agent.

Pattern matching maps natural language to direct agent calls.
No LLM needed for routing — fast, reliable, deterministic.
Falls back to LLM command generation for ambiguous requests.
"""

import re
from brain.agent.system_agents import (
    TerminalAgent, InputAgent, AppAgent, SystemAgent,
    NetworkAgent, FileAgent, DesktopAgent,
    SecurityAgent, VisionAgent, SelfRepairAgent,
    ResearchAgent, DeepResearchAgent, OrchestratorAgent, ServerAgent,
    TransferAgent,
)


class AgentDispatcher:
    """Route user requests to specialized agents.

    Returns (agent_name, action, result) or None if no pattern matched.
    """

    def dispatch(self, text: str) -> dict | None:
        """Try to dispatch directly. Returns result dict or None."""
        q = text.lower().strip()

        # Try each dispatcher in order
        for handler in [
            self._try_self_repair,
            self._try_vision,
            self._try_security,
            self._try_app,
            self._try_desktop,
            self._try_volume,
            self._try_server,
            self._try_network,
            self._try_system,
            self._try_input,
            self._try_transfer,
            self._try_orchestrator,
            self._try_file,
            self._try_research,
        ]:
            result = handler(q, text)
            if result:
                return result

        return None

    # ── App Agent ──────────────────────────────────────────────────

    def _try_app(self, q: str, raw: str) -> dict | None:
        # Open apps
        app_map = {
            # Browsers
            "firefox": ("firefox", ""),
            "chrome": ("google-chrome", ""),
            "google chrome": ("google-chrome", ""),
            "google-chrome": ("google-chrome", ""),
            "chromium": ("chromium", ""),
            "browser": ("google-chrome", ""),
            # Terminal
            "terminal": ("xfce4-terminal", ""),
            "console": ("xfce4-terminal", ""),
            # Files
            "file manager": ("thunar", ""),
            "files": ("thunar", ""),
            "thunar": ("thunar", ""),
            # Editor
            "text editor": ("mousepad", ""),
            "editor": ("mousepad", ""),
            "mousepad": ("mousepad", ""),
            "notepad": ("mousepad", ""),
            # Security
            "burpsuite": ("burpsuite", ""),
            "burp suite": ("burpsuite", ""),
            "burp": ("burpsuite", ""),
            "wireshark": ("wireshark", ""),
            # Dev
            "vscode": ("code", ""),
            "vs code": ("code", ""),
            "visual studio": ("code", ""),
            "code": ("code", ""),
            # Media
            "vlc": ("vlc", ""),
            "media player": ("vlc", ""),
            "gimp": ("gimp", ""),
            "image editor": ("gimp", ""),
            # Utils
            "calculator": ("galculator", ""),
            "calc": ("galculator", ""),
            "settings": ("xfce4-settings-manager", ""),
            "task manager": ("xfce4-taskmanager", ""),
        }

        for trigger, (app, args) in app_map.items():
            if f"open {trigger}" in q or f"launch {trigger}" in q or f"start {trigger}" in q:
                r = AppAgent.launch(app, args)
                return {"agent": "app", "action": f"open {trigger}", "result": r,
                        "summary": f"Done, {trigger} is opening."}

        # Open URL
        url_match = re.search(r'open\s+(https?://\S+)', raw, re.I)
        if url_match:
            url = url_match.group(1)
            r = AppAgent.firefox(url)
            return {"agent": "app", "action": f"open {url}", "result": r,
                    "summary": f"Opening that URL in Firefox."}

        # Close window
        close_match = re.search(r'close\s+(?:the\s+)?(.+?)(?:\s+window)?$', q)
        if close_match and any(w in q for w in ["close"]):
            title = close_match.group(1).strip()
            r = AppAgent.close(title)
            return {"agent": "app", "action": f"close {title}", "result": r,
                    "summary": f"Closed {title}."}

        # List windows
        if any(p in q for p in ["what windows", "list windows", "open windows",
                                 "what's open", "what is open", "show windows"]):
            windows = AppAgent.list_windows()
            titles = [w["title"] for w in windows]
            summary = f"You have {len(windows)} windows open: {', '.join(titles[:5])}."
            return {"agent": "app", "action": "list windows", "result": windows,
                    "summary": summary}

        return None

    # ── Desktop Agent ──────────────────────────────────────────────

    def _try_desktop(self, q: str, raw: str) -> dict | None:
        # Screenshot
        if any(p in q for p in ["screenshot", "screen shot", "capture screen",
                                 "take a picture of the screen"]):
            path = "/tmp/screenshot.png"
            r = DesktopAgent.screenshot(path)
            return {"agent": "desktop", "action": "screenshot", "result": r,
                    "summary": f"Screenshot saved to {path}."}

        # Notifications
        notify_match = re.search(r'(?:send|show)\s+(?:a\s+)?notification\s+(.+)', q)
        if notify_match:
            msg = notify_match.group(1).strip()
            r = DesktopAgent.notify("JARVIS", msg)
            return {"agent": "desktop", "action": "notify", "result": r,
                    "summary": f"Notification sent."}

        # Lock screen
        if any(p in q for p in ["lock screen", "lock the screen", "lock my screen"]):
            r = DesktopAgent.lock_screen()
            return {"agent": "desktop", "action": "lock", "result": r,
                    "summary": "Screen locked."}

        # Resolution
        if any(p in q for p in ["resolution", "screen resolution", "display resolution"]):
            res = DesktopAgent.resolution()
            return {"agent": "desktop", "action": "resolution", "result": {"output": res},
                    "summary": f"Current resolution is {res.strip()}."}

        return None

    # ── Volume ─────────────────────────────────────────────────────

    def _try_volume(self, q: str, raw: str) -> dict | None:
        # Set volume
        vol_match = re.search(r'(?:set\s+)?volume\s+(?:to\s+)?(\d+)', q)
        if vol_match:
            pct = int(vol_match.group(1))
            r = DesktopAgent.volume_set(pct)
            return {"agent": "desktop", "action": f"volume {pct}%", "result": r,
                    "summary": f"Volume set to {pct} percent."}

        if any(p in q for p in ["mute", "silence", "shut up"]):
            r = DesktopAgent.volume_mute()
            return {"agent": "desktop", "action": "mute", "result": r,
                    "summary": "Audio muted."}

        if "unmute" in q:
            r = DesktopAgent.volume_unmute()
            return {"agent": "desktop", "action": "unmute", "result": r,
                    "summary": "Audio unmuted."}

        if any(p in q for p in ["what volume", "current volume", "volume level"]):
            vol = DesktopAgent.volume_get()
            return {"agent": "desktop", "action": "get volume", "result": {"output": vol},
                    "summary": f"Volume is at {vol}."}

        return None

    # ── Network Agent ──────────────────────────────────────────────

    def _try_network(self, q: str, raw: str) -> dict | None:
        # IP address
        if any(p in q for p in ["my ip", "ip address", "what is my ip", "what's my ip"]):
            ip = NetworkAgent.ip()
            return {"agent": "network", "action": "ip", "result": {"output": ip},
                    "summary": f"Your IP address is {ip}."}

        # Ping
        ping_match = re.search(r'ping\s+(\S+)', q)
        if ping_match:
            host = ping_match.group(1)
            r = NetworkAgent.ping(host)
            return {"agent": "network", "action": f"ping {host}", "result": r,
                    "summary": f"Ping to {host} completed." if r["success"] else f"Ping to {host} failed."}

        # Quick nmap scan
        scan_match = re.search(r'(?:scan|nmap)\s+(\S+)', q)
        if scan_match:
            target = scan_match.group(1)
            r = NetworkAgent.quick_scan(target)
            return {"agent": "network", "action": f"scan {target}", "result": r,
                    "summary": None}  # Let LLM summarize nmap output

        # Connections
        if any(p in q for p in ["connections", "listening ports", "open ports",
                                 "what ports", "network connections"]):
            r = NetworkAgent.connections()
            return {"agent": "network", "action": "connections",
                    "result": {"output": r}, "summary": None}

        # DNS
        dns_match = re.search(r'(?:dns|nslookup|dig|resolve)\s+(\S+)', q)
        if dns_match:
            domain = dns_match.group(1)
            ip = NetworkAgent.dns_lookup(domain)
            return {"agent": "network", "action": f"dns {domain}",
                    "result": {"output": ip},
                    "summary": f"{domain} resolves to {ip}."}

        # ── ByteLAN network control ──

        # Discover / list devices
        if any(p in q for p in ["discover devices", "scan network", "scan lan",
                                 "scan my network", "find devices", "devices on network",
                                 "who is on my network", "who's on the network",
                                 "what devices", "connected devices", "network devices"]):
            output = NetworkAgent.discover_devices()
            return {"agent": "network", "action": "discover", "result": {"output": output},
                    "summary": None}

        # Known devices
        if any(p in q for p in ["known devices", "list devices", "my devices",
                                 "show devices", "network map"]):
            output = NetworkAgent.list_known_devices()
            return {"agent": "network", "action": "known devices",
                    "result": {"output": output}, "summary": None}

        # Ping all
        if any(p in q for p in ["ping all", "check all devices", "which devices are online",
                                 "what's online", "device status"]):
            output = NetworkAgent.ping_all()
            return {"agent": "network", "action": "ping all",
                    "result": {"output": output}, "summary": None}

        # Router info
        if any(p in q for p in ["router info", "router status", "about the router",
                                 "openwrt status", "gateway info"]):
            output = NetworkAgent.router_info()
            return {"agent": "network", "action": "router info",
                    "result": {"output": output}, "summary": None}

        # Router clients
        if any(p in q for p in ["router clients", "dhcp leases", "connected to router",
                                 "who's connected"]):
            output = NetworkAgent.router_connected_clients()
            return {"agent": "network", "action": "router clients",
                    "result": {"output": output}, "summary": None}

        # Block device
        block_match = re.search(r'block\s+(?:device\s+)?([0-9a-fA-F:]{17}|\S+)', q)
        if block_match and "block" in q:
            target = block_match.group(1)
            # Try to resolve name to MAC
            for name, dev in NetworkAgent.DEVICES.items():
                if target.lower() in (name, dev.get("name", "").lower()):
                    target = dev.get("mac", target)
                    break
            r = NetworkAgent.router_block_device(target)
            return {"agent": "network", "action": f"block {target}", "result": r,
                    "summary": f"Blocked {target} from the network." if r["success"] else None}

        # Unblock device
        unblock_match = re.search(r'unblock\s+(?:device\s+)?(\S+)', q)
        if unblock_match and "unblock" in q:
            target = unblock_match.group(1)
            r = NetworkAgent.router_unblock_device(target)
            return {"agent": "network", "action": f"unblock {target}", "result": r,
                    "summary": f"Unblocked {target}." if r["success"] else None}

        # VPN status
        if any(p in q for p in ["vpn status", "wireguard status", "vpn", "wireguard"]):
            output = NetworkAgent.vpn_status()
            return {"agent": "network", "action": "vpn",
                    "result": {"output": output}, "summary": None}

        # Speed test
        if any(p in q for p in ["speed test", "speedtest", "bandwidth test",
                                 "internet speed", "test speed"]):
            r = NetworkAgent.bandwidth_test()
            return {"agent": "network", "action": "speedtest", "result": r,
                    "summary": None}

        # Monitor traffic
        if any(p in q for p in ["monitor traffic", "watch traffic", "capture traffic",
                                 "sniff traffic", "tcpdump"]):
            r = NetworkAgent.monitor_traffic()
            return {"agent": "network", "action": "monitor", "result": r,
                    "summary": None}

        # Reconnect WiFi
        if any(p in q for p in ["reconnect wifi", "reconnect to wifi", "fix wifi",
                                 "wifi reconnect"]):
            r = NetworkAgent.wifi_reconnect()
            return {"agent": "network", "action": "reconnect",
                    "result": r, "summary": "Reconnected to ByteLAN." if r["success"] else None}

        # Device info
        info_match = re.search(r'(?:info|details|scan)\s+(?:on|about|for)\s+(\S+)', q)
        if info_match:
            target = info_match.group(1)
            output = NetworkAgent.device_info(target)
            return {"agent": "network", "action": f"device info {target}",
                    "result": {"output": output}, "summary": None}

        return None

    # ── System Agent ───────────────────────────────────────────────

    def _try_system(self, q: str, raw: str) -> dict | None:
        # Install
        install_match = re.search(r'install\s+(\S+)', q)
        if install_match and "install" in q:
            pkg = install_match.group(1)
            r = SystemAgent.install(pkg)
            return {"agent": "system", "action": f"install {pkg}", "result": r,
                    "summary": f"Done, {pkg} has been installed." if r["success"] else f"Failed to install {pkg}."}

        # Services
        svc_match = re.search(r'(start|stop|restart|enable)\s+(?:the\s+)?(\S+?)(?:\s+service)?$', q)
        if svc_match:
            action, svc = svc_match.group(1), svc_match.group(2)
            fn = getattr(SystemAgent, f"service_{action}", None)
            if fn:
                r = fn(svc)
                return {"agent": "system", "action": f"{action} {svc}", "result": r,
                        "summary": f"{svc} service {action}ed." if r["success"] else f"Failed to {action} {svc}."}

        # Kill process
        kill_match = re.search(r'kill\s+(?:the\s+)?(\S+)', q)
        if kill_match and "kill" in q:
            target = kill_match.group(1)
            if target.isdigit():
                r = SystemAgent.kill(int(target))
            else:
                r = SystemAgent.kill_name(target)
            return {"agent": "system", "action": f"kill {target}", "result": r,
                    "summary": f"Killed {target}." if r["success"] else f"Couldn't kill {target}."}

        # System info
        if any(p in q for p in ["system info", "system status", "system information",
                                 "about this machine", "machine info"]):
            info = SystemAgent.info()
            summary = (f"This is {info['hostname']} running kernel {info['kernel']}. "
                       f"Uptime: {info['uptime']}. {info['cpu']} CPU cores.")
            return {"agent": "system", "action": "info", "result": info,
                    "summary": summary}

        # Processes
        if any(p in q for p in ["what processes", "running processes", "top processes",
                                 "what's running", "process list"]):
            ps = SystemAgent.ps()
            return {"agent": "system", "action": "processes",
                    "result": {"output": ps}, "summary": None}

        # Disk
        if any(p in q for p in ["disk space", "disk usage", "storage", "how much space"]):
            disk = TerminalAgent.get_output("df -h / | tail -1")
            parts = disk.split()
            if len(parts) >= 5:
                summary = f"Root partition: {parts[2]} used of {parts[1]}, {parts[3]} free, {parts[4]} full."
            else:
                summary = f"Disk info: {disk}"
            return {"agent": "system", "action": "disk", "result": {"output": disk},
                    "summary": summary}

        # Memory
        if any(p in q for p in ["memory usage", "ram usage", "how much ram", "free memory"]):
            mem = TerminalAgent.get_output("free -h | head -2")
            return {"agent": "system", "action": "memory", "result": {"output": mem},
                    "summary": None}

        # Update
        if q in ("update", "update system", "apt update", "update packages"):
            r = SystemAgent.update()
            return {"agent": "system", "action": "update", "result": r,
                    "summary": "System packages updated." if r["success"] else "Update failed."}

        # Shutdown / reboot / sleep / lock / hibernate
        if any(p in q for p in ["shutdown", "shut down", "power off", "turn off the computer",
                                 "goodnight jarvis", "good night jarvis"]):
            r = SystemAgent.shutdown()
            return {"agent": "system", "action": "shutdown", "result": r,
                    "summary": "Shutting down. Goodbye, Ulrich."}
        if any(p in q for p in ["reboot", "restart the computer", "restart system",
                                 "restart the machine"]):
            r = SystemAgent.reboot()
            return {"agent": "system", "action": "reboot", "result": r,
                    "summary": "Rebooting. I'll be right back."}
        if any(p in q for p in ["go to sleep", "sleep mode", "suspend", "nap time",
                                 "take a nap", "put the computer to sleep",
                                 "put it to sleep"]):
            r = SystemAgent.hybrid_sleep()
            return {"agent": "system", "action": "sleep", "result": r,
                    "summary": "Going to sleep. Wake me when you need me."}
        if any(p in q for p in ["hibernate", "deep sleep"]):
            r = SystemAgent.hibernate()
            return {"agent": "system", "action": "hibernate", "result": r,
                    "summary": "Hibernating. Wake me when you need me."}
        if any(p in q for p in ["lock the screen", "lock screen", "lock the computer",
                                 "lock it", "lock my screen", "lock my computer"]):
            r = SystemAgent.lock()
            return {"agent": "system", "action": "lock", "result": r,
                    "summary": "Screen locked."}
        if "cancel shutdown" in q or "cancel the shutdown" in q:
            r = SystemAgent.cancel_shutdown()
            return {"agent": "system", "action": "cancel_shutdown", "result": r,
                    "summary": "Shutdown cancelled."}

        return None

    # ── Input Agent ────────────────────────────────────────────────

    def _try_input(self, q: str, raw: str) -> dict | None:
        # Type text
        type_match = re.search(r'type\s+["\']?(.+?)["\']?\s*$', raw, re.I)
        if type_match and any(w in q for w in ["type "]):
            text = type_match.group(1)
            r = InputAgent.type_text(text)
            return {"agent": "input", "action": f"type", "result": r,
                    "summary": f"Typed that for you."}

        # Press key
        key_match = re.search(r'press\s+(.+)', q)
        if key_match:
            key = key_match.group(1).strip()
            # Normalize common key names
            key_map = {
                "enter": "Return", "return": "Return",
                "escape": "Escape", "esc": "Escape",
                "tab": "Tab", "space": "space",
                "backspace": "BackSpace", "delete": "Delete",
                "up": "Up", "down": "Down", "left": "Left", "right": "Right",
                "ctrl c": "ctrl+c", "ctrl v": "ctrl+v", "ctrl z": "ctrl+z",
                "ctrl s": "ctrl+s", "ctrl a": "ctrl+a", "ctrl x": "ctrl+x",
                "alt tab": "alt+Tab", "alt f4": "alt+F4",
            }
            key = key_map.get(key, key)
            r = InputAgent.press_key(key)
            return {"agent": "input", "action": f"press {key}", "result": r,
                    "summary": f"Pressed {key}."}

        # Click
        if q in ("click", "left click", "mouse click"):
            r = InputAgent.mouse_click(1)
            return {"agent": "input", "action": "click", "result": r,
                    "summary": "Clicked."}
        if q in ("right click", "right-click"):
            r = InputAgent.mouse_click(3)
            return {"agent": "input", "action": "right click", "result": r,
                    "summary": "Right-clicked."}
        if q in ("double click", "double-click"):
            r = InputAgent.mouse_double_click()
            return {"agent": "input", "action": "double click", "result": r,
                    "summary": "Double-clicked."}

        # Scroll
        if any(p in q for p in ["scroll up"]):
            r = InputAgent.mouse_scroll("up")
            return {"agent": "input", "action": "scroll up", "result": r,
                    "summary": "Scrolled up."}
        if any(p in q for p in ["scroll down"]):
            r = InputAgent.mouse_scroll("down")
            return {"agent": "input", "action": "scroll down", "result": r,
                    "summary": "Scrolled down."}

        # Copy to clipboard
        clip_match = re.search(r'copy\s+["\'](.+?)["\']\s+to\s+clipboard', raw, re.I)
        if clip_match:
            text = clip_match.group(1)
            r = InputAgent.clipboard_copy(text)
            return {"agent": "input", "action": "clipboard copy", "result": r,
                    "summary": "Copied to clipboard."}

        return None

    # ── File Agent ─────────────────────────────────────────────────

    def _try_file(self, q: str, raw: str) -> dict | None:
        # Read file — catch absolute/relative paths
        # "read /etc/hosts", "show me /etc/passwd", "cat ~/.bashrc", "open /var/log/syslog"
        path_match = re.search(r'(?:read|show|cat|display|open|view|look at)\s+(?:me\s+)?(?:the\s+)?(?:file\s+)?([~/][\w/.\-]+)', raw, re.I)
        if path_match:
            path = path_match.group(1)
            output = FileAgent.read(path)
            return {"agent": "file", "action": f"read {path}", "result": {"output": output},
                    "summary": None}

        # "what's in /etc/hosts", "contents of /etc/passwd"
        contents_match = re.search(r"(?:what's in|contents? of|inside)\s+([~/][\w/.\-]+)", raw, re.I)
        if contents_match:
            path = contents_match.group(1)
            output = FileAgent.read(path)
            return {"agent": "file", "action": f"read {path}", "result": {"output": output},
                    "summary": None}

        # List directory
        ls_match = re.search(r'(?:list|show|ls)\s+(?:files\s+)?(?:in\s+)?([~/][\w/.\-]+|\.)', q)
        if ls_match:
            path = ls_match.group(1).strip()
            output = FileAgent.read(path)
            return {"agent": "file", "action": f"list {path}", "result": {"output": output},
                    "summary": None}

        # Show directory tree
        if any(p in q for p in ["directory tree", "folder structure", "tree "]):
            tree_match = re.search(r'(?:tree|structure)\s+(?:of\s+)?([~/][\w/.\-]+|\.)', q)
            path = tree_match.group(1) if tree_match else "."
            output = FileAgent.tree(path)
            return {"agent": "file", "action": f"tree {path}", "result": {"output": output},
                    "summary": None}

        # Edit file — "edit /path old_text new_text" or "change X to Y in /path"
        edit_match = re.search(r'(?:edit|change|replace|modify)\s+(?:in\s+)?([~/][\w/.\-]+)', raw, re.I)
        if edit_match and any(w in q for w in ["edit ", "change ", "replace ", "modify "]):
            path = edit_match.group(1)
            # Need LLM to figure out what to change
            return None  # Let LLM handle complex edits

        # Write/create file
        write_match = re.search(r'(?:create|write|make)\s+(?:a\s+)?(?:file\s+)?(?:called\s+|named\s+|at\s+)?([~/][\w/.\-]+)', raw, re.I)
        if write_match and any(w in q for w in ["create file", "write file", "make file",
                                                  "create a file", "write a file"]):
            path = write_match.group(1)
            return None  # Need LLM to generate content

        # Find files
        find_match = re.search(r'find\s+(?:all\s+)?(.+?)(?:\s+files?)?(?:\s+in\s+([~/][\w/.\-]+))?$', q)
        if find_match and "find" in q:
            pattern = find_match.group(1).strip()
            path = find_match.group(2) or "/"
            if not pattern.startswith("*"):
                pattern = f"*{pattern}*"
            output = FileAgent.search(pattern, path)
            return {"agent": "file", "action": f"find {pattern}", "result": {"output": output},
                    "summary": None}

        # Grep / search content
        grep_match = re.search(r'(?:search|grep|look)\s+(?:for\s+)?["\']?(.+?)["\']?\s+in\s+([~/][\w/.\-]+)', raw, re.I)
        if grep_match:
            pattern = grep_match.group(1)
            path = grep_match.group(2)
            output = FileAgent.grep(pattern, path)
            return {"agent": "file", "action": f"grep {pattern}", "result": {"output": output},
                    "summary": None}

        # Tail logs
        tail_match = re.search(r'(?:tail|last lines?|end of)\s+([~/][\w/.\-]+)', raw, re.I)
        if tail_match:
            path = tail_match.group(1)
            output = FileAgent.tail(path)
            return {"agent": "file", "action": f"tail {path}", "result": {"output": output},
                    "summary": None}

        # Delete
        del_match = re.search(r'(?:delete|remove|rm)\s+(?:the\s+)?(?:file\s+)?([~/][\w/.\-]+)', raw, re.I)
        if del_match and any(w in q for w in ["delete ", "remove ", "rm "]):
            path = del_match.group(1)
            r = FileAgent.delete(path)
            return {"agent": "file", "action": f"delete {path}", "result": r,
                    "summary": f"Deleted {path}." if r["success"] else f"Couldn't delete {path}."}

        # Copy
        cp_match = re.search(r'copy\s+([~/][\w/.\-]+)\s+(?:to\s+)?([~/][\w/.\-]+)', raw, re.I)
        if cp_match:
            r = FileAgent.copy(cp_match.group(1), cp_match.group(2))
            return {"agent": "file", "action": "copy", "result": r,
                    "summary": "Copied." if r["success"] else "Copy failed."}

        # Move/rename
        mv_match = re.search(r'(?:move|rename)\s+([~/][\w/.\-]+)\s+(?:to\s+)?([~/][\w/.\-]+)', raw, re.I)
        if mv_match:
            r = FileAgent.move(mv_match.group(1), mv_match.group(2))
            return {"agent": "file", "action": "move", "result": r,
                    "summary": "Moved." if r["success"] else "Move failed."}

        # Permissions
        chmod_match = re.search(r'(?:chmod|permissions?)\s+(\d{3,4})\s+([~/][\w/.\-]+)', q)
        if chmod_match:
            r = FileAgent.permissions(chmod_match.group(2), chmod_match.group(1))
            return {"agent": "file", "action": "chmod", "result": r,
                    "summary": "Permissions changed." if r["success"] else "Failed."}

        return None

    # ── Security Agent ─────────────────────────────────────────────

    def _try_security(self, q: str, raw: str) -> dict | None:
        # WiFi scan
        if any(p in q for p in ["scan wifi", "wifi scan", "scan networks",
                                 "nearby wifi", "available wifi", "list wifi"]):
            r = SecurityAgent.wifi_interfaces()
            return {"agent": "security", "action": "wifi scan",
                    "result": {"output": r}, "summary": None}

        # WiFi monitor mode
        if any(p in q for p in ["monitor mode", "airmon", "enable monitor"]):
            iface = "wlan0"
            m = re.search(r'on\s+(\w+)', q)
            if m:
                iface = m.group(1)
            r = SecurityAgent.wifi_monitor_start(iface)
            return {"agent": "security", "action": f"monitor {iface}", "result": r,
                    "summary": f"Monitor mode started on {iface}." if r["success"] else None}

        # Airodump
        if any(p in q for p in ["airodump", "capture packets", "sniff wifi"]):
            r = SecurityAgent.wifi_scan_airodump()
            return {"agent": "security", "action": "airodump", "result": r,
                    "summary": None}

        # Deauth
        if "deauth" in q:
            bssid_match = re.search(r'([0-9A-Fa-f:]{17})', raw)
            if bssid_match:
                bssid = bssid_match.group(1)
                r = SecurityAgent.wifi_deauth(bssid)
                return {"agent": "security", "action": f"deauth {bssid}", "result": r,
                        "summary": None}

        # Crack wifi
        if any(p in q for p in ["crack wifi", "crack password", "aircrack",
                                 "hack wifi", "wifi hack", "wifi password",
                                 "break wifi", "crack wpa"]):
            cap = re.search(r'(\S+\.cap)', raw)
            cap_file = cap.group(1) if cap else "/tmp/capture-01.cap"
            r = SecurityAgent.wifi_crack(cap_file)
            return {"agent": "security", "action": "crack wifi", "result": r,
                    "summary": None}

        # Wifite
        if "wifite" in q:
            r = SecurityAgent.wifite()
            return {"agent": "security", "action": "wifite", "result": r,
                    "summary": None}

        # Nikto
        nikto_match = re.search(r'nikto\s+(\S+)', q)
        if nikto_match:
            target = nikto_match.group(1)
            r = SecurityAgent.nikto(target)
            return {"agent": "security", "action": f"nikto {target}", "result": r,
                    "summary": None}

        # Gobuster
        gobuster_match = re.search(r'gobuster\s+(\S+)', q)
        if gobuster_match:
            target = gobuster_match.group(1)
            r = SecurityAgent.gobuster(target)
            return {"agent": "security", "action": f"gobuster {target}", "result": r,
                    "summary": None}

        # SQLmap
        sqlmap_match = re.search(r'sqlmap\s+(\S+)', q)
        if sqlmap_match:
            target = sqlmap_match.group(1)
            r = SecurityAgent.sqlmap(target)
            return {"agent": "security", "action": f"sqlmap {target}", "result": r,
                    "summary": None}

        # Metasploit
        if any(p in q for p in ["metasploit", "msfconsole", "msf "]):
            r = AppAgent.launch("xfce4-terminal", "-e msfconsole")
            return {"agent": "security", "action": "metasploit", "result": r,
                    "summary": "Opening Metasploit in a terminal."}

        # Hydra
        hydra_match = re.search(r'hydra\s+(\S+)', q)
        if hydra_match or "brute force" in q:
            return None  # Too complex for pattern match — let LLM handle

        # OSINT / whois
        whois_match = re.search(r'whois\s+(\S+)', q)
        if whois_match:
            domain = whois_match.group(1)
            r = SecurityAgent.whois(domain)
            return {"agent": "security", "action": f"whois {domain}",
                    "result": {"output": r}, "summary": None}

        return None

    # ── Vision Agent ───────────────────────────────────────────────

    def _try_vision(self, q: str, raw: str) -> dict | None:
        # Take photo
        if any(p in q for p in ["take a photo", "take photo", "capture photo",
                                 "take a picture", "take picture", "webcam photo",
                                 "camera photo", "snap a photo", "use camera",
                                 "use webcam", "use the camera", "use the webcam"]):
            path = "/tmp/camera.jpg"
            r = VisionAgent.capture_photo(path)
            return {"agent": "vision", "action": "photo", "result": r,
                    "summary": f"Photo captured and saved." if r["success"] else "Camera capture failed."}

        # Record video
        if any(p in q for p in ["record video", "capture video", "webcam video",
                                 "camera video", "film "]):
            dur_match = re.search(r'(\d+)\s*(?:second|sec)', q)
            duration = int(dur_match.group(1)) if dur_match else 5
            r = VisionAgent.capture_video(duration=duration)
            return {"agent": "vision", "action": "video", "result": r,
                    "summary": f"Recorded {duration} seconds of video." if r["success"] else "Video capture failed."}

        # List cameras
        if any(p in q for p in ["list cameras", "available cameras", "camera devices",
                                 "what cameras", "which camera"]):
            output = VisionAgent.list_cameras()
            return {"agent": "vision", "action": "list cameras",
                    "result": {"output": output}, "summary": None}

        # Record screen
        if any(p in q for p in ["record screen", "screen record", "screencast",
                                 "record my screen"]):
            dur_match = re.search(r'(\d+)\s*(?:second|sec)', q)
            duration = int(dur_match.group(1)) if dur_match else 10
            r = VisionAgent.screen_record(duration=duration)
            return {"agent": "vision", "action": "screen record", "result": r,
                    "summary": f"Recorded {duration} seconds of screen." if r["success"] else "Screen recording failed."}

        # What do you see (camera)
        if any(p in q for p in ["what do you see", "what can you see",
                                 "look around", "what's in front"]):
            r = VisionAgent.capture_photo()
            if r["success"]:
                return {"agent": "vision", "action": "look", "result": r,
                        "summary": "I captured a photo from the camera."}
            return {"agent": "vision", "action": "look", "result": r,
                    "summary": "My camera isn't available right now."}

        return None

    # ── Self-Repair Agent ──────────────────────────────────────────

    def _try_self_repair(self, q: str, raw: str) -> dict | None:
        # Health check
        if any(p in q for p in ["health check", "self check", "diagnose yourself",
                                 "check yourself", "are you ok", "status check",
                                 "self diagnostic", "run diagnostic"]):
            checks = SelfRepairAgent.health_check()
            parts = []
            for k, v in checks.items():
                if isinstance(v, str):
                    parts.append(f"{k}: {v}")
            summary = "Health check complete. " + ". ".join(parts[:5])
            return {"agent": "self_repair", "action": "health check",
                    "result": checks, "summary": summary}

        # Restart server
        if any(p in q for p in ["restart yourself", "restart server", "restart jarvis",
                                 "reboot yourself", "fix yourself"]):
            r = SelfRepairAgent.restart_server()
            return {"agent": "self_repair", "action": "restart", "result": r,
                    "summary": "I've restarted myself." if r["success"] else "Restart failed."}

        # Restart ollama
        if any(p in q for p in ["restart ollama", "fix ollama", "ollama not working"]):
            r = SelfRepairAgent.restart_ollama()
            return {"agent": "self_repair", "action": "restart ollama", "result": r,
                    "summary": "Ollama restarted." if r["success"] else "Couldn't restart Ollama."}

        # Fix audio
        if any(p in q for p in ["fix audio", "fix sound", "fix speaker", "no sound",
                                 "can't hear", "audio not working", "fix my audio"]):
            r = SelfRepairAgent.fix_audio()
            return {"agent": "self_repair", "action": "fix audio", "result": r,
                    "summary": "I've reset the audio system. Try again."}

        # Fix mic
        if any(p in q for p in ["fix mic", "fix microphone", "mic not working",
                                 "can't hear me", "fix my mic"]):
            r = SelfRepairAgent.fix_mic()
            return {"agent": "self_repair", "action": "fix mic", "result": r,
                    "summary": "I've reset the microphone. Try speaking again."}

        # Check logs
        if any(p in q for p in ["check logs", "show logs", "your logs", "jarvis logs",
                                 "error logs", "what went wrong"]):
            logs = SelfRepairAgent.check_logs()
            return {"agent": "self_repair", "action": "logs",
                    "result": {"output": logs}, "summary": None}

        # Update
        if any(p in q for p in ["update yourself", "self update", "pull latest",
                                 "update jarvis", "upgrade yourself"]):
            r = SelfRepairAgent.self_update()
            changelog = r.get("changelog", "")
            if r["success"] and changelog and "No new updates" not in changelog:
                summary = f"I've updated myself. Here's what changed: {r.get('output', '')}. {changelog[:100]}"
            elif r["success"]:
                summary = "I'm already up to date. No changes."
            else:
                summary = "Update failed."
            return {"agent": "self_repair", "action": "update", "result": r,
                    "summary": summary}

        # Check for updates
        if any(p in q for p in ["check for updates", "any updates", "new updates"]):
            r = SelfRepairAgent.auto_update_check()
            if r["has_updates"]:
                summary = f"Yes, there are {r['count']} updates available. Want me to apply them?"
            else:
                summary = "No updates available. I'm on the latest version."
            return {"agent": "self_repair", "action": "check updates", "result": r,
                    "summary": summary}

        # Clear cache
        if any(p in q for p in ["clear cache", "clear your cache", "reset cache"]):
            r = SelfRepairAgent.clear_cache()
            return {"agent": "self_repair", "action": "clear cache", "result": r,
                    "summary": "Cache cleared."}

        return None

    # ── Server Agent (Proxmox) ─────────────────────────────────────

    def _try_server(self, q: str, raw: str) -> dict | None:
        # Resolve VM/container name or ID from the query
        def _resolve(text):
            for name in list(ServerAgent.VMS.values()) + list(ServerAgent.CONTAINERS.values()):
                if name.lower() in text.lower():
                    return ServerAgent.resolve_id(name)
            # Try number
            import re as _re
            m = _re.search(r'\b(\d{3})\b', text)
            if m:
                return int(m.group(1))
            return None

        # Server status
        if any(p in q for p in ["server status", "proxmox status", "server info",
                                 "proxmox info", "how is the server"]):
            output = ServerAgent.status()
            return {"agent": "server", "action": "status",
                    "result": {"output": output}, "summary": None}

        # List everything
        if any(p in q for p in ["server resources", "proxmox resources", "all vms and containers",
                                 "what's running on the server", "server overview"]):
            output = ServerAgent.resources()
            return {"agent": "server", "action": "resources",
                    "result": {"output": output}, "summary": None}

        # List VMs
        if any(p in q for p in ["list vms", "show vms", "virtual machines", "my vms"]):
            output = ServerAgent.list_vms()
            return {"agent": "server", "action": "list vms",
                    "result": {"output": output}, "summary": None}

        # List containers
        if any(p in q for p in ["list containers", "show containers", "my containers",
                                 "lxc containers"]):
            output = ServerAgent.list_containers()
            return {"agent": "server", "action": "list containers",
                    "result": {"output": output}, "summary": None}

        # Start VM/container
        if any(w in q for w in ["start ", "boot ", "power on "]):
            vmid = _resolve(raw)
            if vmid:
                name = {**ServerAgent.VMS, **ServerAgent.CONTAINERS}.get(vmid, str(vmid))
                if vmid in ServerAgent.VMS:
                    r = ServerAgent.start_vm(vmid)
                else:
                    r = ServerAgent.start_container(vmid)
                return {"agent": "server", "action": f"start {name}", "result": r,
                        "summary": f"Starting {name}." if r["success"] else None}

        # Stop VM/container
        if any(w in q for w in ["stop ", "shutdown ", "power off "]) and any(
            n.lower() in q for n in list(ServerAgent.VMS.values()) + list(ServerAgent.CONTAINERS.values()) + [str(i) for i in list(ServerAgent.VMS) + list(ServerAgent.CONTAINERS)]
        ):
            vmid = _resolve(raw)
            if vmid:
                name = {**ServerAgent.VMS, **ServerAgent.CONTAINERS}.get(vmid, str(vmid))
                if vmid in ServerAgent.VMS:
                    r = ServerAgent.stop_vm(vmid)
                else:
                    r = ServerAgent.stop_container(vmid)
                return {"agent": "server", "action": f"stop {name}", "result": r,
                        "summary": f"Stopping {name}." if r["success"] else None}

        # Restart VM/container
        if "restart " in q:
            vmid = _resolve(raw)
            if vmid:
                name = {**ServerAgent.VMS, **ServerAgent.CONTAINERS}.get(vmid, str(vmid))
                if vmid in ServerAgent.VMS:
                    r = ServerAgent.reboot_vm(vmid)
                else:
                    r = ServerAgent.restart_container(vmid)
                return {"agent": "server", "action": f"restart {name}", "result": r,
                        "summary": f"Restarting {name}." if r["success"] else None}

        # Run command inside container
        exec_match = re.search(r'(?:run|exec|execute)\s+(.+?)\s+(?:in|on|inside)\s+(\w+)', raw, re.I)
        if exec_match:
            cmd = exec_match.group(1).strip()
            target = exec_match.group(2).strip()
            vmid = _resolve(target)
            if vmid and vmid in ServerAgent.CONTAINERS:
                r = ServerAgent.container_exec(vmid, cmd)
                return {"agent": "server", "action": f"exec on {target}", "result": r,
                        "summary": None}

        # Storage
        if any(p in q for p in ["storage status", "disk space on server", "server storage",
                                 "proxmox storage"]):
            output = ServerAgent.storage_status()
            return {"agent": "server", "action": "storage",
                    "result": {"output": output}, "summary": None}

        # Backup
        if "backup" in q:
            vmid = _resolve(raw)
            if vmid:
                name = {**ServerAgent.VMS, **ServerAgent.CONTAINERS}.get(vmid, str(vmid))
                if vmid in ServerAgent.VMS:
                    r = ServerAgent.backup_vm(vmid)
                else:
                    r = ServerAgent.backup_container(vmid)
                return {"agent": "server", "action": f"backup {name}", "result": r,
                        "summary": f"Backing up {name}." if r["success"] else None}

        # List backups
        if any(p in q for p in ["list backups", "show backups", "my backups"]):
            output = ServerAgent.list_backups()
            return {"agent": "server", "action": "list backups",
                    "result": {"output": output}, "summary": None}

        # SSH command on server
        if any(p in q for p in ["on the server", "on proxmox", "on the proxmox"]):
            cmd_match = re.search(r'(?:run|execute)\s+(.+?)\s+on\s+(?:the\s+)?(?:server|proxmox)', raw, re.I)
            if cmd_match:
                cmd = cmd_match.group(1).strip()
                r = ServerAgent.ssh_command(cmd)
                return {"agent": "server", "action": f"ssh: {cmd[:40]}", "result": r,
                        "summary": None}

        # Create container
        if any(p in q for p in ["create container", "create lxc", "new container",
                                 "spin up container", "deploy container"]):
            name_match = re.search(r'(?:called|named|name)\s+(\w+)', q)
            name = name_match.group(1) if name_match else "new-container"
            r = ServerAgent.create_container(name)
            return {"agent": "server", "action": f"create {name}", "result": r,
                    "summary": f"Creating container {name}." if r["success"] else None}

        # Update container
        if "update" in q:
            vmid = _resolve(raw)
            if vmid and vmid in ServerAgent.CONTAINERS:
                name = ServerAgent.CONTAINERS.get(vmid, str(vmid))
                r = ServerAgent.container_exec(vmid, "apt update && apt upgrade -y")
                return {"agent": "server", "action": f"update {name}", "result": r,
                        "summary": f"Updating {name}." if r["success"] else None}

        return None

    # ── Transfer Agent ─────────────────────────────────────────────

    def _try_transfer(self, q: str, raw: str) -> dict | None:
        # Download URL
        url_match = re.search(r'(https?://\S+)', raw)

        if any(w in q for w in ["download ", "fetch ", "grab ", "get "]) and url_match:
            url = url_match.group(1)

            # YouTube / video
            if any(d in url for d in ["youtube.com", "youtu.be", "vimeo.com", "tiktok.com"]):
                if any(w in q for w in ["audio", "mp3", "music", "song"]):
                    r = TransferAgent.download_audio(url)
                    return {"agent": "transfer", "action": "download audio",
                            "result": r, "summary": "Audio download started." if r["success"] else None}
                r = TransferAgent.download_video(url)
                return {"agent": "transfer", "action": "download video",
                        "result": r, "summary": "Video download started." if r["success"] else None}

            # Regular file
            r = TransferAgent.download(url)
            return {"agent": "transfer", "action": "download",
                    "result": r, "summary": "Download complete." if r["success"] else None}

        # Download video/audio by description
        if any(p in q for p in ["download video", "download the video",
                                 "download audio", "download music", "download song"]):
            if url_match:
                url = url_match.group(1)
                if "audio" in q or "music" in q or "song" in q:
                    r = TransferAgent.download_audio(url)
                else:
                    r = TransferAgent.download_video(url)
                return {"agent": "transfer", "action": "download media",
                        "result": r, "summary": "Download started." if r["success"] else None}

        # Torrent
        magnet_match = re.search(r'(magnet:\S+)', raw)
        if magnet_match or "torrent" in q:
            magnet = magnet_match.group(1) if magnet_match else ""
            if magnet:
                r = TransferAgent.download_torrent(magnet)
                return {"agent": "transfer", "action": "torrent",
                        "result": r, "summary": "Torrent download started." if r["success"] else None}

        # Upload to server
        if any(p in q for p in ["upload", "send to server", "copy to server",
                                 "transfer to server", "upload to proxmox"]) and "server" in q:
            path_match = re.search(r'(?:upload|send|copy|transfer)\s+(\S+)', raw, re.I)
            if path_match:
                path = path_match.group(1)
                r = TransferAgent.upload_to_server(path)
                return {"agent": "transfer", "action": "upload to server",
                        "result": r, "summary": "Uploaded to server." if r["success"] else None}

        # Download from server
        if any(p in q for p in ["download from server", "get from server",
                                 "copy from server", "fetch from server"]):
            path_match = re.search(r'(?:from server)\s+(\S+)', raw, re.I)
            if path_match:
                path = path_match.group(1)
                r = TransferAgent.download_from_server(path)
                return {"agent": "transfer", "action": "download from server",
                        "result": r, "summary": "Downloaded from server." if r["success"] else None}

        # Sync
        if "sync" in q or "rsync" in q:
            parts = re.findall(r'(\S+)', raw)
            src_dst = [p for p in parts if "/" in p]
            if len(src_dst) >= 2:
                r = TransferAgent.sync(src_dst[0], src_dst[1])
                return {"agent": "transfer", "action": "sync",
                        "result": r, "summary": "Sync complete." if r["success"] else None}

        # SCP upload
        if "scp" in q:
            scp_parts = re.findall(r'(\S+)', raw)
            paths = [p for p in scp_parts if "/" in p or "@" in p]
            if len(paths) >= 2:
                r = TransferAgent.upload_scp(paths[0], paths[1])
                return {"agent": "transfer", "action": "scp",
                        "result": r, "summary": "Transfer complete." if r["success"] else None}

        # List downloads
        if any(p in q for p in ["list downloads", "show downloads", "my downloads",
                                 "what's downloaded", "download folder"]):
            output = TransferAgent.list_downloads()
            return {"agent": "transfer", "action": "list downloads",
                    "result": {"output": output}, "summary": None}

        # Download status
        if any(p in q for p in ["download status", "is it downloading", "download progress"]):
            output = TransferAgent.download_status()
            if output:
                return {"agent": "transfer", "action": "status",
                        "result": {"output": output}, "summary": None}
            return {"agent": "transfer", "action": "status",
                    "result": {"output": ""}, "summary": "No active downloads."}

        return None

    # ── Orchestrator Agent ──────────────────────────────────────────

    def _try_orchestrator(self, q: str, raw: str) -> dict | None:
        # Playbooks — multi-step operations
        # "full recon on 10.0.0.1", "system audit", "security audit"
        if any(p in q for p in ["full recon", "full scan", "recon on", "reconnaissance"]):
            target_match = re.search(r'(?:on|of|for)\s+(\S+)', q)
            target = target_match.group(1) if target_match else "localhost"
            r = OrchestratorAgent.run_playbook("recon", {"target": target})
            return {"agent": "orchestrator", "action": f"recon {target}",
                    "result": r, "summary": None}

        if any(p in q for p in ["system audit", "audit system", "full system check",
                                 "check everything", "system report"]):
            r = OrchestratorAgent.run_playbook("system_audit")
            return {"agent": "orchestrator", "action": "system audit",
                    "result": r, "summary": None}

        if any(p in q for p in ["security audit", "audit security", "security check",
                                 "check security", "hardening check"]):
            r = OrchestratorAgent.run_playbook("security_audit")
            return {"agent": "orchestrator", "action": "security audit",
                    "result": r, "summary": None}

        if any(p in q for p in ["cleanup", "clean up", "clean system", "free space",
                                 "system cleanup"]):
            r = OrchestratorAgent.run_playbook("cleanup")
            return {"agent": "orchestrator", "action": "cleanup",
                    "result": r, "summary": None}

        if any(p in q for p in ["web recon", "website recon", "web reconnaissance"]):
            target_match = re.search(r'(?:on|of|for)\s+(\S+)', q)
            target = target_match.group(1) if target_match else ""
            if target:
                r = OrchestratorAgent.run_playbook("web_recon", {"target": target})
                return {"agent": "orchestrator", "action": f"web recon {target}",
                        "result": r, "summary": None}

        # "list playbooks", "available playbooks"
        if any(p in q for p in ["list playbooks", "available playbooks", "show playbooks"]):
            pbs = OrchestratorAgent.list_playbooks()
            descs = []
            for name in pbs:
                pb = OrchestratorAgent.PLAYBOOKS[name]
                descs.append(f"{name}: {pb['description']} ({len(pb['steps'])} steps)")
            return {"agent": "orchestrator", "action": "list playbooks",
                    "result": {"output": "\n".join(descs)},
                    "summary": f"I have {len(pbs)} playbooks: {', '.join(pbs)}."}

        # "run all these commands: cmd1, cmd2, cmd3"
        if any(p in q for p in ["run these commands", "run all these", "execute these",
                                 "run commands"]):
            # Extract commands from the rest of the text
            cmds_match = re.search(r'commands?:?\s*(.+)', raw, re.I | re.DOTALL)
            if cmds_match:
                cmds_text = cmds_match.group(1)
                cmds = [c.strip() for c in re.split(r'[,;\n]+', cmds_text) if c.strip()]
                if cmds:
                    r = OrchestratorAgent.run_commands(cmds)
                    return {"agent": "orchestrator", "action": "multi-command",
                            "result": r, "summary": None}

        return None

    # ── Research Agent ─────────────────────────────────────────────

    def _try_research(self, q: str, raw: str) -> dict | None:
        # Web search
        search_match = re.search(r'(?:search|google|look up|search for|search the web for)\s+(.+)', q)
        if search_match and any(w in q for w in ["search ", "google ", "look up "]):
            query = search_match.group(1).strip()
            output = ResearchAgent.search_and_summarize(query)
            return {"agent": "research", "action": f"search: {query}",
                    "result": {"output": output}, "summary": None}

        # Quick answer
        if any(p in q for p in ["what is the latest", "current price of",
                                 "who won", "what happened", "news about"]):
            answer = ResearchAgent.quick_answer(q)
            return {"agent": "research", "action": "quick answer",
                    "result": {"output": answer}, "summary": None}

        # How to
        how_match = re.search(r'how (?:do i|to|can i)\s+(.+)', q)
        if how_match:
            topic = how_match.group(1).strip()
            result = DeepResearchAgent.how_to(topic)
            return {"agent": "deep_research", "action": f"how to: {topic}",
                    "result": result, "summary": None}

        # What command / what tool
        if any(p in q for p in ["what command", "what tool", "which command",
                                 "which tool", "right command", "correct command"]):
            result = DeepResearchAgent.find_command(q)
            return {"agent": "deep_research", "action": "find command",
                    "result": {"output": result}, "summary": None}

        return None
