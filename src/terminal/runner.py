"""TerminalRunner — detect visual/background commands and parse natural language.

This handles the fast-path terminal commands that don't need an LLM:
opening GUI apps, background operations, and simple command extraction.
"""

import re
from typing import Optional

# ── GUI / visual applications ─────────────────────────────────────────

_VISUAL_APPS = {
    # Browsers
    "firefox", "chromium", "chrome", "google-chrome", "brave", "vivaldi",
    "tor-browser",
    # File managers
    "nautilus", "thunar", "nemo", "dolphin", "pcmanfm", "ranger",
    # Editors / IDEs
    "code", "vscode", "codium", "gedit", "kate", "sublime", "subl",
    "atom", "geany", "mousepad", "xed",
    # Media
    "vlc", "mpv", "totem", "eog", "feh", "sxiv", "gimp", "inkscape",
    "audacity", "obs", "kdenlive", "blender",
    # Security GUI
    "burpsuite", "burp", "wireshark", "zenmap", "ghidra", "ida",
    "maltego", "ettercap",
    # Communication
    "discord", "slack", "telegram", "signal", "thunderbird",
    # System
    "htop", "btop", "gnome-system-monitor", "virtualbox", "virt-manager",
    # Terminal emulators (when launched explicitly)
    "xterm", "kitty", "alacritty", "wezterm", "terminator", "tilix",
    # Other
    "libreoffice", "okular", "evince", "zathura", "flameshot",
}

_VISUAL_PHRASES = [
    "open", "launch", "start", "show me", "bring up", "run gui",
    "open up", "fire up", "pull up",
]

# ── Background operation indicators ──────────────────────────────────

_BG_INDICATORS = [
    "in the background", "in background", "background", "detach",
    "keep running", "don't wait", "run async", "daemonize",
    "nohup", "&",
]

_BG_COMMANDS = {
    "serve", "server", "watch", "monitor", "tail", "stream",
    "listen", "proxy", "tunnel", "ngrok", "localtunnel",
}

# ── Command extraction patterns ──────────────────────────────────────

_COMMAND_PATTERNS: list[tuple[re.Pattern, int]] = [
    # "run <command>"
    (re.compile(r"\b(?:run|execute|exec)\s+[`'\"]?(.+?)[`'\"]?\s*$", re.IGNORECASE), 1),
    # "$ <command>" or "> <command>"
    (re.compile(r"^[\$>]\s*(.+)$"), 1),
    # "`command`" (backtick-wrapped)
    (re.compile(r"`([^`]+)`"), 1),
    # "type <command>"
    (re.compile(r"\btype\s+[`'\"]?(.+?)[`'\"]?\s*$", re.IGNORECASE), 1),
]

# Natural-language → command mappings for common requests
_NL_COMMANDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:what(?:'s| is) my) ip\b", re.I), "curl -s ifconfig.me"),
    (re.compile(r"\bwho(?:ami| am i)\b", re.I), "whoami"),
    (re.compile(r"\b(?:disk|storage) (?:space|usage)\b", re.I), "df -h"),
    (re.compile(r"\b(?:memory|ram) usage\b", re.I), "free -h"),
    (re.compile(r"\b(?:cpu|processor) (?:info|usage)\b", re.I), "lscpu"),
    (re.compile(r"\buptime\b", re.I), "uptime"),
    (re.compile(r"\b(?:kernel|uname)\b", re.I), "uname -a"),
    (re.compile(r"\blist (?:all )?(?:running )?(?:processes|procs)\b", re.I), "ps aux"),
    (re.compile(r"\b(?:network|net) interfaces?\b", re.I), "ip a"),
    (re.compile(r"\blistening ports?\b", re.I), "ss -tlnp"),
    (re.compile(r"\b(?:current|working) dir(?:ectory)?\b", re.I), "pwd"),
    (re.compile(r"\bshow (?:all )?env(?:ironment)?(?: variables?)?\b", re.I), "env"),
]


class TerminalRunner:
    """Parse natural-language queries into shell operations."""

    # ── Detection ─────────────────────────────────────────────────────

    def is_visual_command(self, query: str) -> bool:
        """True if the query is asking to open a GUI application."""
        q = query.lower().strip()

        # Check if any visual phrase + app name combo matches
        for phrase in _VISUAL_PHRASES:
            if q.startswith(phrase):
                remainder = q[len(phrase):].strip()
                first_word = remainder.split()[0] if remainder.split() else ""
                if first_word in _VISUAL_APPS:
                    return True

        # Check if the query is just an app name
        words = q.split()
        if len(words) <= 3 and words[0] in _VISUAL_APPS:
            return True

        return False

    def is_background_command(self, query: str) -> bool:
        """True if the query implies a background/long-running operation."""
        q = query.lower().strip()
        for indicator in _BG_INDICATORS:
            if indicator in q:
                return True
        first_word = q.split()[0] if q.split() else ""
        return first_word in _BG_COMMANDS

    # ── Parsing ───────────────────────────────────────────────────────

    def parse_command(self, query: str) -> Optional[str]:
        """Extract a shell command from natural language.

        Returns the command string if one can be parsed, else None.
        """
        q = query.strip()

        # Direct pattern extraction
        for pattern, group in _COMMAND_PATTERNS:
            m = pattern.search(q)
            if m:
                cmd = m.group(group).strip()
                if cmd:
                    return cmd

        # Natural-language mappings
        for pattern, cmd in _NL_COMMANDS:
            if pattern.search(q):
                return cmd

        # "open <app>" → launch command
        lower = q.lower()
        for phrase in _VISUAL_PHRASES:
            if lower.startswith(phrase):
                remainder = q[len(phrase):].strip()
                if remainder:
                    app = remainder.split()[0].lower()
                    if app in _VISUAL_APPS:
                        args = remainder[len(app):].strip()
                        return f"{app} {args}".strip() if args else app

        return None

    def build_background_command(self, cmd: str) -> str:
        """Wrap a command for background execution."""
        # Strip existing trailing &
        cmd = cmd.rstrip().rstrip("&").rstrip()
        return f"nohup {cmd} > /dev/null 2>&1 &"

    def build_visual_command(self, cmd: str) -> str:
        """Wrap a GUI command so it detaches from the terminal."""
        cmd = cmd.rstrip().rstrip("&").rstrip()
        return f"setsid {cmd} > /dev/null 2>&1 &"

    def run_visual(self, cmd: str, title: str = "") -> str:
        """Launch a visual/GUI command and return status message."""
        import subprocess
        wrapped = self.build_visual_command(cmd)
        try:
            subprocess.Popen(wrapped, shell=True, start_new_session=True)
            return f"Launched: {cmd}"
        except Exception as e:
            return f"Failed to launch: {e}"
