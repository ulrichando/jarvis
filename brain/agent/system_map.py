"""JARVIS System Map — indexes everything installed on the machine.

Builds a searchable cache of:
- GUI apps (from .desktop files)
- CLI tools (from PATH)
- Kali security tools
- System services
- Important directories

JARVIS queries this instead of running `find`/`grep` every time.
Rebuilds on startup, cached in SQLite.
"""

import os
import subprocess
import sqlite3
import time
from pathlib import Path

DB_PATH = os.path.expanduser("~/.jarvis/data/system_map.db")


def _run(cmd: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


class SystemMap:
    """Searchable index of everything on this machine."""

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS apps (
                name TEXT PRIMARY KEY,
                exec TEXT,
                category TEXT DEFAULT '',
                type TEXT DEFAULT 'gui'
            );
            CREATE TABLE IF NOT EXISTS tools (
                name TEXT PRIMARY KEY,
                path TEXT,
                category TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS services (
                name TEXT PRIMARY KEY,
                status TEXT DEFAULT 'unknown'
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self.conn.commit()

    def needs_rebuild(self) -> bool:
        """Check if map needs rebuilding (older than 24h or empty)."""
        r = self.conn.execute("SELECT value FROM meta WHERE key='last_build'").fetchone()
        if not r:
            return True
        try:
            last = float(r[0])
            return (time.time() - last) > 86400  # 24 hours
        except Exception:
            return True

    def build(self):
        """Scan the system and build the index."""
        print("[JARVIS] Building system map...")
        start = time.time()

        self._index_gui_apps()
        self._index_kali_tools()
        self._index_cli_tools()
        self._index_services()

        self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_build', ?)",
                          (str(time.time()),))
        self.conn.commit()

        stats = self.stats()
        elapsed = int((time.time() - start) * 1000)
        print(f"[JARVIS] System map: {stats['apps']} apps, {stats['tools']} tools, "
              f"{stats['services']} services ({elapsed}ms)")

    def _index_gui_apps(self):
        """Index all .desktop GUI applications."""
        self.conn.execute("DELETE FROM apps")
        apps = []
        for desktop in Path("/usr/share/applications").glob("*.desktop"):
            try:
                content = desktop.read_text(errors="replace")
                name = exec_cmd = category = ""
                for line in content.split("\n"):
                    if line.startswith("Name=") and not name:
                        name = line[5:].strip()
                    elif line.startswith("Exec=") and not exec_cmd:
                        exec_cmd = line[5:].strip().split()[0]
                    elif line.startswith("Categories="):
                        category = line[12:].strip()
                if name and exec_cmd:
                    apps.append((name, exec_cmd, category, "gui"))
            except Exception:
                continue
        self.conn.executemany(
            "INSERT OR REPLACE INTO apps VALUES (?, ?, ?, ?)", apps)
        self.conn.commit()

    def _index_kali_tools(self):
        """Index Kali-specific security tools."""
        tools_output = _run("dpkg -l 'kali-tools-*' 2>/dev/null | grep '^ii' | awk '{print $2}'")
        for pkg in tools_output.split("\n"):
            pkg = pkg.strip()
            if pkg:
                self.conn.execute(
                    "INSERT OR REPLACE INTO tools VALUES (?, ?, ?)",
                    (pkg, "", "kali"))

        # Also index common security tools by name
        sec_tools = _run(
            "which nmap nikto sqlmap gobuster hydra john hashcat aircrack-ng "
            "wifite msfconsole msfvenom responder enum4linux smbclient "
            "tcpdump wireshark burpsuite zaproxy dirb wpscan netcat "
            "socat masscan recon-ng theHarvester maltego "
            "2>/dev/null | sort -u")
        for line in sec_tools.split("\n"):
            line = line.strip()
            if line:
                name = os.path.basename(line)
                self.conn.execute(
                    "INSERT OR REPLACE INTO tools VALUES (?, ?, ?)",
                    (name, line, "security"))
        self.conn.commit()

    def _index_cli_tools(self):
        """Index common CLI tools in PATH."""
        # Don't index all 6000 — just the useful ones
        categories = {
            "editor": "nano vim vi mousepad gedit kate code emacs",
            "browser": "firefox google-chrome chromium brave-browser",
            "media": "vlc mpv ffplay ffmpeg ffprobe aplay paplay",
            "network": "nmap ping traceroute dig nslookup curl wget ss netstat ip arp",
            "files": "find grep sed awk cat head tail less more wc file stat tree",
            "archive": "tar gzip gunzip zip unzip 7z bzip2 xz",
            "dev": "python3 pip node npm cargo rustc gcc g++ make cmake git docker",
            "system": "systemctl journalctl top htop ps kill pkill df du free mount umount",
            "remote": "ssh scp sftp rsync sshpass",
            "download": "wget curl aria2c yt-dlp youtube-dl transmission-cli",
            "image": "gimp inkscape scrot import convert identify",
            "pdf": "evince atril okular zathura pdftotext",
            "office": "libreoffice",
        }
        for category, tools_str in categories.items():
            for tool in tools_str.split():
                path = _run(f"which {tool} 2>/dev/null")
                if path:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO tools VALUES (?, ?, ?)",
                        (tool, path, category))
        self.conn.commit()

    def _index_services(self):
        """Index systemd services."""
        self.conn.execute("DELETE FROM services")
        output = _run("systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null | head -100")
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0].replace(".service", "")
                status = parts[2]  # active/inactive
                self.conn.execute(
                    "INSERT OR REPLACE INTO services VALUES (?, ?)",
                    (name, status))
        self.conn.commit()

    # ── Search ──

    def search(self, query: str) -> str:
        """Search everything — apps, tools, services."""
        q = f"%{query}%"
        results = []

        # Search apps
        rows = self.conn.execute(
            "SELECT name, exec, category FROM apps WHERE "
            "LOWER(name) LIKE LOWER(?) OR LOWER(exec) LIKE LOWER(?) LIMIT 10",
            (q, q)).fetchall()
        if rows:
            results.append("APPS:")
            for r in rows:
                results.append(f"  {r['name']} → {r['exec']} [{r['category'][:30]}]")

        # Search tools
        rows = self.conn.execute(
            "SELECT name, path, category FROM tools WHERE "
            "LOWER(name) LIKE LOWER(?) LIMIT 10",
            (q,)).fetchall()
        if rows:
            results.append("TOOLS:")
            for r in rows:
                results.append(f"  {r['name']} → {r['path']} [{r['category']}]")

        # Search services
        rows = self.conn.execute(
            "SELECT name, status FROM services WHERE "
            "LOWER(name) LIKE LOWER(?) LIMIT 10",
            (q,)).fetchall()
        if rows:
            results.append("SERVICES:")
            for r in rows:
                results.append(f"  {r['name']} [{r['status']}]")

        return "\n".join(results) if results else f"Nothing found for '{query}'"

    def find_app(self, query: str) -> dict | None:
        """Find a specific app by name. Returns {name, exec} or None."""
        row = self.conn.execute(
            "SELECT name, exec FROM apps WHERE "
            "LOWER(name) LIKE LOWER(?) OR LOWER(exec) LIKE LOWER(?) LIMIT 1",
            (f"%{query}%", f"%{query}%")).fetchone()
        if row:
            return {"name": row["name"], "exec": row["exec"]}
        return None

    def find_tool(self, query: str) -> str | None:
        """Find a CLI tool path."""
        row = self.conn.execute(
            "SELECT path FROM tools WHERE LOWER(name) LIKE LOWER(?) LIMIT 1",
            (f"%{query}%",)).fetchone()
        return row["path"] if row else None

    def list_category(self, category: str) -> str:
        """List all tools in a category."""
        rows = self.conn.execute(
            "SELECT name, path FROM tools WHERE category = ? ORDER BY name",
            (category,)).fetchall()
        if rows:
            return "\n".join(f"  {r['name']} → {r['path']}" for r in rows)

        rows = self.conn.execute(
            "SELECT name, exec FROM apps WHERE LOWER(category) LIKE LOWER(?) ORDER BY name LIMIT 20",
            (f"%{category}%",)).fetchall()
        if rows:
            return "\n".join(f"  {r['name']} → {r['exec']}" for r in rows)

        return f"No tools found in category '{category}'"

    def stats(self) -> dict:
        apps = self.conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0]
        tools = self.conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
        services = self.conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        return {"apps": apps, "tools": tools, "services": services}

    def close(self):
        self.conn.close()


# Singleton
_instance = None

def get_system_map() -> SystemMap:
    global _instance
    if _instance is None:
        _instance = SystemMap()
        if _instance.needs_rebuild():
            _instance.build()
    return _instance
