#!/usr/bin/env python3
"""JARVIS CLI — autonomous AI agent in your terminal.

Terminal UX features:
- Minimal, content-focused interface
- Braille spinner animation for thinking/tool calls
- Streaming markdown with safe-boundary rendering
- 91 slash commands via CommandRegistry
- Session persistence and resume
- Tool call visualization (single-line spinner → markdown result)
- Token usage footer per turn
- Ctrl+C cancellation, pipe/stdin support
"""

import sys
import os
import asyncio
import time
import re
import subprocess
import threading
import logging
import warnings
import types
from typing import Optional, List

import typer

try:
    from wcwidth import wcswidth as _wcswidth
except ImportError:
    _wcswidth = None


# ── Enum choices for CLI options ─────────────────────────────────────────────
from enum import Enum

class ThemeChoice(str, Enum):
    dark = "dark"
    light = "light"
    ghost = "ghost"
    auto = "auto"

class ModeChoice(str, Enum):
    normal = "normal"
    agent = "agent"
    cli = "cli"
    berbon = "berbon"
    plan = "plan"

class EffortChoice(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    max = "max"

class OutputFormat(str, Enum):
    text = "text"
    json = "json"
    stream_json = "stream-json"

class ThinkingMode(str, Enum):
    enabled = "enabled"
    adaptive = "adaptive"
    disabled = "disabled"

class PermissionMode(str, Enum):
    default = "default"
    bypass = "bypass"
    accept_edits = "accept-edits"
    plan = "plan"


def _display_width(text: str) -> int:
    """Get the display width of text, accounting for wide characters (CJK, emoji)."""
    if _wcswidth is not None:
        w = _wcswidth(text)
        if w >= 0:
            return w
    return len(text)


def _truncate_display(text: str, max_width: int) -> str:
    """Truncate text to fit within max_width display columns."""
    if _display_width(text) <= max_width:
        return text
    result = ""
    width = 0
    for char in text:
        cw = _display_width(char)
        if width + cw > max_width:
            break
        result += char
        width += cw
    return result

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Suppress noisy library logs from polluting the terminal
logging.getLogger("numexpr").setLevel(logging.ERROR)
logging.getLogger("numexpr.utils").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module="numexpr")
# Suppress ResourceWarning (unclosed sockets from async HTTP clients) — not
# actionable by the user and would corrupt the input frame if printed to stderr.
warnings.filterwarnings("ignore", category=ResourceWarning)
# Suppress PyGIDeprecationWarning from GTK/GLib bindings (gi package) —
# "GLib.unix_signal_add_full is deprecated; use GLibUnix.signal_add_full"
warnings.filterwarnings("ignore", module="gi")

# ── Keybinding System (src/keybindings) ─────────────────────────────
from src.keybindings import KeybindingResolver, ParsedKeystroke, DEFAULT_BINDINGS
_keybinding_resolver = KeybindingResolver()


def _char_to_keystroke(ch: str) -> ParsedKeystroke:
    """Convert a raw terminal character to a ParsedKeystroke for keybinding resolution."""
    ks = ParsedKeystroke()
    if len(ch) == 1 and ord(ch) < 32:
        # Control character: Ctrl+letter
        ks.ctrl = True
        ks.key = chr(ord(ch) + 96)  # e.g. \x03 -> 'c', \x0c -> 'l'
    elif ch == "\x1b":
        ks.key = "escape"
    elif ch == "\n" or ch == "\r":
        ks.key = "enter"
    elif ch == "\t":
        ks.key = "tab"
    elif ch == "\x7f" or ch == "\x08":
        ks.key = "backspace"
    else:
        ks.key = ch
    return ks


def resolve_keybinding(context: str, ch: str) -> str | None:
    """Resolve a raw character to a keybinding action. Returns action name or None."""
    ks = _char_to_keystroke(ch)
    return _keybinding_resolver.resolve(context, ks)

# ── Vim Mode System (src/vim) ───────────────────────────────────────
from src.vim.types import (
    VimState, InsertState, NormalState, IdleCommand,
    create_initial_vim_state, create_initial_persistent_state,
    PersistentState, OPERATORS, SIMPLE_MOTIONS,
)
from src.vim.transitions import enter_insert, enter_normal
from src.vim.motions import resolve_motion, is_inclusive_motion
from src.vim.operators import delete_range, yank_range, change_range, TextRange
from src.vim.textObjects import inner_word, a_word

# ── State Manager (src/state) ──────────────────────────────────────
from src.state import get_state_manager as _get_state_manager

# ── Theme Detection (src/utils/theme) ──────────────────────────────
from src.utils.theme import (
    get_theme as _get_src_theme, theme_color_to_ansi,
    ThemeName, THEME_NAMES,
)
from src.utils.effort import (
    EffortLevel, get_effort_level_description, get_effort_suffix,
    convert_effort_value_to_level, parse_effort_value,
)

# ── Theme System ─────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"

# Theme definitions — colors adapt to dark/light terminal backgrounds
THEMES = {
    "dark": {
        "primary": "\033[36m",      # Cyan — prompts, highlights
        "success": "\033[32m",      # Green — completions, confirmations
        "warning": "\033[33m",      # Yellow — warnings, modes
        "error": "\033[31m",        # Red — errors
        "accent": "\033[34m",       # Blue — spinner, info
        "secondary": "\033[35m",    # Magenta — italic text, links
        "muted": "\033[90m",        # Grey — dim text, separators
        "text": "\033[97m",         # White — headings
        "code_bg": "\033[48;5;236m",  # Dark background for code
        "code_fg": "\033[38;5;252m",  # Light text in code blocks
    },
    "light": {
        "primary": "\033[34m",      # Blue — prompts, highlights
        "success": "\033[32m",      # Green — completions
        "warning": "\033[33m",      # Yellow — warnings
        "error": "\033[31m",        # Red — errors
        "accent": "\033[36m",       # Cyan — spinner, info
        "secondary": "\033[35m",    # Magenta — italic
        "muted": "\033[37m",        # Light grey — dim text
        "text": "\033[30m",         # Black — headings
        "code_bg": "\033[48;5;255m",  # Light background for code
        "code_fg": "\033[38;5;235m",  # Dark text in code blocks
    },
    "ghost": {
        "primary":   "\033[38;5;247m",    # Silver — prompts, highlights
        "success":   "\033[38;5;115m",    # Muted green — completions
        "warning":   "\033[38;5;179m",    # Muted amber — warnings
        "error":     "\033[38;5;167m",    # Muted red — errors
        "accent":    "\033[38;5;111m",    # Slate blue — spinner, info
        "secondary": "\033[38;5;140m",    # Muted violet — italic text
        "muted":     "\033[38;5;240m",    # Dark grey — dim text
        "text":      "\033[38;5;252m",    # Near-white — headings
        "code_bg":   "\033[48;5;234m",    # Very dark background for code
        "code_fg":   "\033[38;5;248m",    # Silver text in code blocks
    },
}

# Active theme colors — set once at startup, referenced everywhere
_active_theme = "dark"

def _load_theme() -> str:
    """Load theme preference from settings."""
    try:
        from pathlib import Path
        settings_path = Path.home() / ".jarvis" / "settings.json"
        if settings_path.exists():
            import json
            settings = json.loads(settings_path.read_text())
            t = settings.get("theme", "dark")
            if t == "auto":
                # Detect from COLORFGBG env var (format: "fg;bg")
                colorfgbg = os.environ.get("COLORFGBG", "")
                if colorfgbg:
                    parts = colorfgbg.split(";")
                    if len(parts) >= 2:
                        bg = int(parts[-1]) if parts[-1].isdigit() else 0
                        return "light" if bg > 8 else "dark"
                return "dark"
            return t if t in THEMES else "dark"
    except Exception:
        pass
    return "dark"

def _apply_theme(theme_name: str = ""):
    """Apply theme colors to module-level variables."""
    global _active_theme, CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA, GREY, WHITE, BG_DARK
    if theme_name:
        _active_theme = theme_name
    t = THEMES.get(_active_theme, THEMES["dark"])
    CYAN = t["primary"]
    GREEN = t["success"]
    YELLOW = t["warning"]
    RED = t["error"]
    BLUE = t["accent"]
    MAGENTA = t["secondary"]
    GREY = t["muted"]
    WHITE = t["text"]
    BG_DARK = t["code_bg"]

# Color variables — initialized with defaults, overridden by _apply_theme()
CYAN = GREEN = YELLOW = RED = BLUE = MAGENTA = GREY = WHITE = BG_DARK = ""

# Initialize theme on module load
_active_theme = _load_theme()
_apply_theme(_active_theme)

# Braille spinner frames (same as JARVIS)
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _write(text: str):
    sys.stdout.write(text)
    sys.stdout.flush()


def _writeln(text: str = ""):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _clear_line():
    _write("\r\033[K")


# ── Spinner ──────────────────────────────────────────────────────────

class Spinner:
    """Braille spinner for thinking/tool call indicators."""

    def __init__(self):
        self._frame = 0
        self._active = False
        self._start: float = 0.0

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        s = int(seconds)
        return f"{s // 60}m{s % 60}s" if s >= 60 else f"{s}s"

    @staticmethod
    def _term_width() -> int:
        import shutil
        return shutil.get_terminal_size((80, 24)).columns

    def _render(self, icon: str, icon_color: str, label: str, elapsed: float | None = None):
        """Render a spinner/status line with optional right-aligned elapsed time."""
        _clear_line()
        prefix = f"  {icon_color}{icon}{RESET} {label}"
        if elapsed is not None:
            elapsed_str = self._fmt_elapsed(elapsed)
            # Strip ANSI codes for length measurement
            import re as _re
            visible_len = len(_re.sub(r'\033\[[^m]*m', '', prefix))
            pad = max(1, self._term_width() - visible_len - len(elapsed_str) - 1)
            _write(f"{prefix}{' ' * pad}{DIM}{elapsed_str}{RESET}")
        else:
            _write(prefix)
        self._active = True

    def tick(self, label: str):
        frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        self._frame += 1
        if self._start == 0.0:
            self._start = time.monotonic()
        self._render(frame, BLUE, label, time.monotonic() - self._start)

    def done(self, label: str):
        elapsed = time.monotonic() - self._start if self._start else None
        self._render("✔", GREEN, label, elapsed)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._active = False
        self._start = 0.0

    def fail(self, label: str):
        elapsed = time.monotonic() - self._start if self._start else None
        self._render("✘", RED, label, elapsed)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._active = False
        self._start = 0.0

    def clear(self):
        if self._active:
            _clear_line()
            self._active = False
            self._start = 0.0


# ── Markdown Rendering (simplified, terminal-safe) ────────────────────

# Language keyword maps for syntax highlighting
_PYTHON_KEYWORDS = {"def", "class", "if", "elif", "else", "for", "while", "return",
                    "import", "from", "as", "try", "except", "finally", "with", "yield",
                    "raise", "pass", "break", "continue", "lambda", "and", "or", "not",
                    "in", "is", "None", "True", "False", "self", "async", "await"}
_JS_KEYWORDS = {"function", "const", "let", "var", "if", "else", "for", "while", "return",
                "import", "export", "from", "class", "new", "this", "async", "await",
                "try", "catch", "finally", "throw", "typeof", "instanceof", "null",
                "undefined", "true", "false", "switch", "case", "default", "break"}
_RUST_KEYWORDS = {"fn", "let", "mut", "if", "else", "for", "while", "loop", "return",
                  "use", "mod", "pub", "struct", "enum", "impl", "trait", "match",
                  "self", "Self", "async", "await", "move", "ref", "where", "type"}
_BASH_KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "do", "done", "while",
                  "case", "esac", "function", "return", "exit", "echo", "export",
                  "local", "readonly", "declare", "set", "unset", "source"}


def _highlight_line(line: str, keywords: set) -> str:
    """Highlight keywords and strings in a single line."""
    import re as _re

    # Highlight strings first (green)
    line = _re.sub(r'(f?"[^"]*")', f'\033[32m\\1\033[0m', line)
    line = _re.sub(r"(f?'[^']*')", f'\033[32m\\1\033[0m', line)

    # Highlight numbers (magenta)
    line = _re.sub(r'\b(\d+\.?\d*)\b', f'\033[35m\\1\033[0m', line)

    # Highlight keywords (yellow bold)
    for kw in keywords:
        line = _re.sub(rf'\b({kw})\b', f'\033[1;33m\\1\033[0m', line)

    # Highlight decorators/attributes (cyan) for python
    line = _re.sub(r'(@\w+)', f'\033[36m\\1\033[0m', line)

    return line


def _highlight_code(code: str, lang: str) -> str:
    """Apply syntax highlighting to a code line based on language."""
    lang = lang.lower().strip()

    # Select keyword set
    keywords = set()
    if lang in ("python", "py"):
        keywords = _PYTHON_KEYWORDS
    elif lang in ("javascript", "js", "typescript", "ts", "jsx", "tsx"):
        keywords = _JS_KEYWORDS
    elif lang in ("rust", "rs"):
        keywords = _RUST_KEYWORDS
    elif lang in ("bash", "sh", "zsh", "shell"):
        keywords = _BASH_KEYWORDS

    if not keywords:
        return code  # No highlighting for unknown languages

    # Highlight comments (# for python/bash, // for js/rust)
    comment_char = "#" if lang in ("python", "py", "bash", "sh", "zsh", "shell") else "//"
    if comment_char in code:
        idx = code.index(comment_char)
        # Make sure it's not inside a string (simple check)
        before = code[:idx]
        if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
            return _highlight_line(before, keywords) + f"\033[90m{code[idx:]}\033[0m"

    return _highlight_line(code, keywords)


def render_markdown(text: str) -> str:
    """Render markdown to ANSI-styled terminal output."""
    lines = text.split("\n")
    output = []
    in_code = False
    code_lang = ""

    for line in lines:
        # Code fence
        if line.strip().startswith("```"):
            if not in_code:
                code_lang = line.strip()[3:].strip()
                output.append(f"  {GREY}╭─ {code_lang}{RESET}")
                in_code = True
            else:
                output.append(f"  {GREY}╰─{RESET}")
                in_code = False
                code_lang = ""
            continue

        if in_code:
            highlighted = _highlight_code(line, code_lang)
            output.append(f"  {BG_DARK}  {highlighted}  {RESET}")
            continue

        # Headings
        if line.startswith("### "):
            output.append(f"\n  {BLUE}{line[4:]}{RESET}")
        elif line.startswith("## "):
            output.append(f"\n  {WHITE}{BOLD}{line[3:]}{RESET}")
        elif line.startswith("# "):
            output.append(f"\n  {CYAN}{BOLD}{line[2:]}{RESET}")
        # Bullet lists
        elif line.strip().startswith("- ") or line.strip().startswith("* "):
            indent = len(line) - len(line.lstrip())
            content = line.strip()[2:]
            output.append(f"{'  ' * (indent // 2 + 1)}  • {_inline_format(content)}")
        # Numbered lists
        elif re.match(r'\s*\d+\.\s', line):
            output.append(f"  {_inline_format(line)}")
        # Block quotes
        elif line.strip().startswith("> "):
            output.append(f"  {GREY}│ {line.strip()[2:]}{RESET}")
        # Horizontal rule
        elif line.strip() in ("---", "***", "___"):
            output.append(f"  {GREY}{'─' * 50}{RESET}")
        # Normal text
        else:
            output.append(f"  {_inline_format(line)}")

    return "\n".join(output)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting (bold, italic, code)."""
    # Inline code
    text = re.sub(r'`([^`]+)`', f'{GREEN}\\1{RESET}', text)
    # Bold
    text = re.sub(r'\*\*([^*]+)\*\*', f'{YELLOW}{BOLD}\\1{RESET}', text)
    # Italic
    text = re.sub(r'\*([^*]+)\*', f'{MAGENTA}\\1{RESET}', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', f'{BLUE}{UNDERLINE}\\1{RESET}', text)
    return text


# ── Tool Call Formatting ─────────────────────────────────────────────

def format_tool_call(name: str, args: dict) -> str:
    """Format a tool call as a single-line spinner label."""
    if name == "bash":
        cmd = args.get("command", "")
        if len(cmd) > 80:
            cmd = cmd[:80] + "..."
        return f"Running `{name}`: {cmd}"
    elif name == "read_file":
        return f"Reading `{args.get('path', '')}`"
    elif name == "write_file":
        path = args.get("path", "")
        lines = args.get("content", "").count("\n") + 1
        return f"Writing `{path}` ({lines} lines)"
    elif name == "edit_file":
        return f"Editing `{args.get('path', '')}`"
    elif name == "search_files":
        return f"Searching: {args.get('pattern', '')}"
    elif name == "web_search":
        return f"Searching web: {args.get('query', '')}"
    elif name == "web_fetch":
        return f"Fetching: {args.get('url', '')}"
    elif name == "think":
        thought = args.get("thought", "")[:60]
        return f"Thinking: {thought}"
    elif name == "dispatch":
        atype = args.get("agent_type", "?")
        task = args.get("task", "")[:50]
        return f"Spawning {atype} agent: {task}"
    elif name.startswith("mcp_"):
        return f"MCP tool `{name}`"
    else:
        return f"Running `{name}`"


def format_tool_result(name: str, result: str) -> str:
    """Format tool result as markdown code block."""
    if not result or result.strip() == "(no output)":
        return ""
    lines = result.strip().split("\n")
    truncated = len(lines) > 30 or len(result) > 3000

    # Persist full output to ~/.jarvis/tmp/ when truncating
    tmp_path = None
    if truncated:
        try:
            import tempfile as _tf
            _jarvis_tmp = os.path.expanduser("~/.jarvis/tmp")
            os.makedirs(_jarvis_tmp, exist_ok=True)
            with _tf.NamedTemporaryFile(
                mode="w", suffix=".txt",
                prefix=f"tool_{name}_",
                dir=_jarvis_tmp, delete=False,
            ) as _f:
                _f.write(result.strip())
                tmp_path = _f.name
        except Exception:
            pass

    if len(lines) > 30:
        display = "\n".join(lines[:25]) + f"\n... ({len(lines) - 25} more lines)"
    else:
        display = result.strip()
    if len(display) > 3000:
        display = display[:3000] + "\n... (truncated)"
    if tmp_path:
        display += f"\n[full output → {tmp_path}]"
    return f"### Tool `{name}`\n\n```text\n{display}\n```"


# ── Standalone Brain ─────────────────────────────────────────────────

class StandaloneBrain:
    """Connects to JARVIS server (shared Brain) or falls back to local Brain."""

    def __init__(self):
        self.brain = None
        self._is_full_brain = True
        self._server_mode = False
        self._server_url, self._ws_url = self._resolve_server_url()
        self._ws = None
        self._session = None

    @staticmethod
    def _resolve_server_url() -> tuple[str, str]:
        """Return (http_url, ws_url) for the brain to use.

        Priority:
          1. JARVIS_SERVER env var  (e.g. http://jarvis.local:8765)
          2. ~/.jarvis/remote.json  {"brain_url": "http://jarvis.local:8765"}
          3. localhost:8765 fallback
        """
        import json as _j
        from pathlib import Path

        # Env override
        env = os.environ.get("JARVIS_SERVER", "").strip()
        if env:
            base = env.rstrip("/")
            return base, base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

        # remote.json (shared with desktop)
        remote_path = Path.home() / ".jarvis" / "remote.json"
        if remote_path.exists():
            try:
                cfg = _j.loads(remote_path.read_text())
                brain_url = (cfg.get("brain_url") or "").strip().rstrip("/")
                if brain_url:
                    ws = brain_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
                    return brain_url, ws
            except Exception:
                pass

        return "http://localhost:8765", "ws://localhost:8765/ws"

    async def connect(self) -> bool:
        # Try connecting to running JARVIS server first (shared Brain)
        try:
            import aiohttp
            _http_timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._server_url}/api/ready", timeout=_http_timeout) as resp:
                    if resp.status == 200:
                        self._server_mode = True
                        self._is_full_brain = False
                        # Connect WebSocket — use heartbeat, no deprecated timeout kwarg
                        self._session = aiohttp.ClientSession()
                        self._ws = await asyncio.wait_for(
                            self._session.ws_connect(self._ws_url, heartbeat=20.0),
                            timeout=5.0,
                        )
                        return True
        except (ConnectionError, asyncio.TimeoutError, OSError) as e:
            logging.getLogger("jarvis.cli").debug("Server connection failed: %s", e)
        except Exception as e:
            logging.getLogger("jarvis.cli").debug("Unexpected connection error: %s", e)

        # Server not running — start local Brain
        prev_level = logging.root.level
        logging.disable(logging.WARNING)
        import warnings
        warnings.filterwarnings("ignore")

        try:
            from src.brain import Brain
            self.brain = Brain(quiet=True)
            self._is_full_brain = True
            logging.disable(logging.NOTSET)
            logging.root.setLevel(prev_level)
            return True
        except Exception as e:
            logging.disable(logging.NOTSET)
            logging.root.setLevel(prev_level)
            _writeln(f"  {RED}Brain failed: {e}{RESET}")
            return False

    async def query(self, text: str) -> str:
        if self._server_mode:
            import aiohttp, json
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._server_url}/api/think",
                    json={"query": text},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    data = await resp.json()
                    return data.get("response", "No response")
        if self.brain:
            self.brain._current_channel = "cli"
            if not hasattr(self.brain, '_channel_state'):
                self.brain._channel_state = {}
            self.brain._channel_state["cli"] = True
        return await self.brain.think(text)

    async def _reconnect_ws(self) -> bool:
        """Re-establish the WebSocket connection after it goes stale."""
        try:
            if self._ws and not self._ws.closed:
                await self._ws.close()
        except Exception:
            pass
        try:
            self._ws = await self._session.ws_connect(self._ws_url)
            return True
        except Exception:
            return False

    async def query_stream(self, text: str):
        if self._server_mode and self._ws:
            import json
            # Reconnect if the WS is stale (server restarted)
            if self._ws.closed:
                if not await self._reconnect_ws():
                    yield {"type": "error", "content": "Server disconnected — could not reconnect"}
                    return
            # Send query; retry once with a fresh connection on transport error
            try:
                await self._ws.send_json({"type": "query", "text": text})
            except Exception:
                if not await self._reconnect_ws():
                    yield {"type": "error", "content": "Server disconnected — could not reconnect"}
                    return
                try:
                    await self._ws.send_json({"type": "query", "text": text})
                except Exception as e:
                    yield {"type": "error", "content": str(e)}
                    return
            # Track what we've already shown to prevent duplicates
            _streamed = False
            async for msg in self._ws:
                if msg.type == 1:  # TEXT
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type", "")
                        if msg_type == "stream":
                            # Real-time text chunks — display these
                            yield {"type": "text", "content": data.get("content", "")}
                            _streamed = True
                        elif msg_type == "message":
                            # Full/partial message from server
                            if not _streamed:
                                # No stream chunks received — show message content
                                yield {"type": "text", "content": data.get("content", "")}
                            # If partial, keep listening. If final, we're done.
                            if not data.get("partial"):
                                yield {"type": "done", "content": data.get("content", "")}
                                return
                        elif msg_type == "tool_call":
                            yield {"type": "tool_call", "name": data.get("name", ""), "args": data.get("args", {})}
                        elif msg_type == "tool_result":
                            yield {"type": "tool_result", "name": data.get("name", ""), "content": data.get("content", "")}
                        elif msg_type == "status":
                            pass
                        elif msg_type == "error":
                            yield {"type": "error", "content": data.get("error", "Unknown error")}
                            return
                    except Exception:
                        continue
                elif msg.type in (8, 256):  # CLOSE, ERROR
                    break
            yield {"type": "done", "content": ""}
            return

        # Local Brain streaming
        async for event in self.brain.think_stream(text):
            yield event

    async def close(self):
        try:
            if self._server_mode:
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                if hasattr(self, '_session') and not self._session.closed:
                    await self._session.close()
                # Give aiohttp time to clean up
                import asyncio
                await asyncio.sleep(0.1)
            elif self.brain:
                if hasattr(self.brain, "mcp"):
                    self.brain.mcp.stop_all()
                if hasattr(self.brain, "memory"):
                    self.brain.memory.save()
        except Exception:
            pass  # Suppress cleanup errors on exit
        finally:
            # Clear CLI channel state so brain doesn't count this channel as active
            try:
                if self.brain and hasattr(self.brain, '_channel_state'):
                    self.brain._channel_state["cli"] = False
            except Exception:
                pass


# ── CLI Entry ────────────────────────────────────────────────────────

__version__ = "2.0.0"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"JARVIS v{__version__}")
        raise typer.Exit()


_app = typer.Typer(
    name="jarvis",
    help="JARVIS — autonomous AI agent",
    add_completion=True,
    pretty_exceptions_enable=False,
    epilog=(
        "Examples:\n"
        "  jarvis                        Start interactive session\n"
        "  jarvis -c                     Continue last session\n"
        "  jarvis -r my-project          Resume named session\n"
        "  jarvis -p 'list files'        One-shot print mode\n"
        "  cat log.txt | jarvis -p 'analyze this'"
    ),
)


@_app.command()
def _typer_entry(
    # Session
    continue_last: bool = typer.Option(False, "-c", "--continue", help="Continue the most recent session"),
    resume: Optional[str] = typer.Option(None, "-r", "--resume", metavar="NAME", help="Resume a session by name or ID"),
    name: str = typer.Option("", "-n", "--name", help="Name for the new session"),
    # Query mode
    print_mode: Optional[str] = typer.Option(None, "-p", "--print", metavar="QUERY", help="One-shot mode: run query and print result"),
    mode: Optional[ModeChoice] = typer.Option(None, "-m", "--mode", envvar="JARVIS_MODE", help="Starting mode"),
    # Positional query words
    query: Optional[List[str]] = typer.Argument(None, help="Initial query", envvar="JARVIS_QUERY"),
    # Server / theme
    serve: bool = typer.Option(False, "--serve", help="Start as MCP server (stdio mode)"),
    theme: Optional[ThemeChoice] = typer.Option(None, "--theme", envvar="JARVIS_THEME", help="Color theme"),
    # Model & effort
    model: Optional[str] = typer.Option(None, "--model", metavar="MODEL", envvar="JARVIS_MODEL", help="Override model (opus/sonnet/haiku or full name)"),
    effort: Optional[EffortChoice] = typer.Option(None, "--effort", envvar="JARVIS_EFFORT", help="Response effort level"),
    fallback_model: Optional[str] = typer.Option(None, "--fallback-model", metavar="MODEL", help="Fallback model on overload"),
    # Output
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--output-format", help="Output format"),
    json_schema: Optional[str] = typer.Option(None, "--json-schema", metavar="SCHEMA", help="JSON schema for structured output"),
    # Limits
    max_turns: Optional[int] = typer.Option(None, "--max-turns", metavar="N", help="Max agentic turns in non-interactive mode"),
    max_budget_usd: Optional[float] = typer.Option(None, "--max-budget-usd", metavar="USD", help="Max spend for this session"),
    # System prompt
    system_prompt: Optional[str] = typer.Option(None, "--system-prompt", metavar="PROMPT", help="Custom system prompt"),
    system_prompt_file: Optional[typer.FileText] = typer.Option(None, "--system-prompt-file", help="Read system prompt from file"),
    append_system_prompt: Optional[str] = typer.Option(None, "--append-system-prompt", metavar="PROMPT", help="Append to default system prompt"),
    # Advanced
    bare: bool = typer.Option(False, "--bare", help="Minimal mode: skip hooks, plugins, MCP discovery"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose output (show full tool results)"),
    debug: Optional[str] = typer.Option(None, "--debug", metavar="FILTER", help="Debug mode (all / api,hooks,tools)"),
    thinking: Optional[ThinkingMode] = typer.Option(None, "--thinking", help="Thinking mode"),
    # Permissions
    permission_mode: Optional[PermissionMode] = typer.Option(None, "--permission-mode", envvar="JARVIS_PERMISSION_MODE", help="Permission mode"),
    dangerously_skip_permissions: bool = typer.Option(False, "--dangerously-skip-permissions", help="Skip all permission checks"),
    # Tools
    tools: Optional[List[str]] = typer.Option(None, "--tools", metavar="TOOL", help="Specify available tools"),
    allowed_tools: Optional[List[str]] = typer.Option(None, "--allowed-tools", metavar="TOOL", help="Tool allowlist"),
    disallowed_tools: Optional[List[str]] = typer.Option(None, "--disallowed-tools", metavar="TOOL", help="Tool denylist"),
    # MCP
    mcp_config: Optional[str] = typer.Option(None, "--mcp-config", metavar="FILE", help="MCP server config file"),
    # Worktree
    worktree: Optional[str] = typer.Option(None, "-w", "--worktree", metavar="NAME", help="Create git worktree (use 'auto' for automatic name)"),
    # Version (eager — handled before any other processing)
    version: Optional[bool] = typer.Option(None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit"),
):
    """JARVIS — autonomous AI agent."""
    # Read file content if system_prompt_file was provided (typer.FileText)
    _spf_content = system_prompt_file.read() if system_prompt_file else None
    args = types.SimpleNamespace(
        continue_last=continue_last,
        resume=resume,
        name=name,
        print_mode=print_mode,
        mode=mode.value if mode else "normal",
        query=query or [],
        serve=serve,
        theme=theme.value if theme else None,
        model=model,
        effort=effort.value if effort else None,
        fallback_model=fallback_model,
        output_format=output_format.value if output_format else "text",
        json_schema=json_schema,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        system_prompt=system_prompt,
        system_prompt_file=_spf_content,   # now a str (file already read)
        append_system_prompt=append_system_prompt,
        bare=bare,
        verbose=verbose,
        debug=debug,
        thinking=thinking.value if thinking else None,
        permission_mode=permission_mode.value if permission_mode else None,
        dangerously_skip_permissions=dangerously_skip_permissions,
        tools=tools,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        mcp_config=mcp_config,
        worktree=worktree,
    )
    asyncio.run(main(args))


# Commands with fixed enumerable options — shown as visual pickers in the CLI
_COMMAND_OPTIONS: dict[str, list[tuple[str, str]]] = {
    # ── Core toggles ───────────────────────────────────────────────
    "effort": [
        ("low",    "Minimal reasoning, fastest responses"),
        ("medium", "Balanced depth and speed"),
        ("high",   "Deep reasoning, thorough answers"),
        ("max",    "Maximum effort, extended thinking"),
    ],
    "mode": [
        ("normal", "Standard conversational mode"),
        ("agent",  "Autonomous agent with tools"),
        ("plan",   "Read-only planning mode"),
        ("berbon", "Fully autonomous mode"),
        ("cli",    "CLI-optimised mode"),
    ],
    "theme": [
        ("dark",  "Dark terminal theme"),
        ("light", "Light terminal theme"),
        ("auto",  "Follow system preference"),
    ],
    "permissions": [
        ("read_only",  "Read files only, no writes or commands"),
        ("standard",   "Normal tool access"),
        ("full",       "Full tool access including writes"),
        ("dangerous",  "Unrestricted access"),
    ],
    "debug": [
        ("on",    "Enable all debug logging"),
        ("off",   "Disable debug logging"),
        ("api",   "Toggle API/provider logs"),
        ("hooks", "Toggle hooks logs"),
        ("tools", "Toggle tool execution logs"),
        ("mcp",   "Toggle MCP logs"),
    ],
    "voice": [
        ("on",       "Enable voice input/output"),
        ("off",      "Disable voice"),
        ("language", "Change voice language"),
    ],
    "vim": [
        ("on",     "Enable vim keybindings"),
        ("off",    "Disable vim keybindings"),
        ("toggle", "Toggle vim mode"),
    ],
    "fast": [
        ("on",     "Enable fast mode (less reasoning)"),
        ("off",    "Disable fast mode"),
        ("toggle", "Toggle fast mode"),
    ],
    "sandbox": [
        ("on",     "Enable command sandboxing"),
        ("off",    "Disable sandboxing"),
        ("status", "Show current sandbox state"),
    ],
    "statusline": [
        ("on",      "Show status line"),
        ("off",     "Hide status line"),
        ("default", "Reset to default"),
    ],
    "color": [
        ("cyan",    "Cyan accent (default)"),
        ("green",   "Green accent"),
        ("blue",    "Blue accent"),
        ("purple",  "Purple accent"),
        ("orange",  "Orange accent"),
        ("red",     "Red accent"),
        ("white",   "White accent"),
        ("yellow",  "Yellow accent"),
    ],
    "verbose": [
        ("on",     "Enable verbose output"),
        ("off",    "Disable verbose output"),
        ("toggle", "Toggle verbose mode"),
    ],
    "privacy": [
        ("show",    "Show privacy settings"),
        ("disable", "Disable telemetry"),
        ("enable",  "Enable telemetry"),
    ],
    "self-modify": [
        ("propose", "Propose improvements to JARVIS code"),
        ("apply",   "Apply a proposed change"),
    ],
    "benchmark": [
        ("llm",   "Benchmark LLM response time"),
        ("tools", "Benchmark tool execution speed"),
        ("all",   "Run all benchmarks"),
    ],
    "extra-usage": [
        ("on",     "Enable extra usage (continue past limits)"),
        ("off",    "Disable extra usage"),
        ("status", "Show extra usage status"),
    ],
    "monitor": [
        ("on",     "Enable security monitoring"),
        ("off",    "Disable security monitoring"),
        ("status", "Show monitor status"),
    ],
    "bridge": [
        ("start",    "Start the remote bridge server"),
        ("stop",     "Stop the remote bridge server"),
        ("status",   "Show bridge connection status"),
        ("url",      "Show connection URL"),
        ("sessions", "List active remote sessions"),
    ],
    "ide": [
        ("connect",    "Connect to IDE (VS Code / JetBrains)"),
        ("disconnect", "Disconnect from IDE"),
        ("status",     "Show IDE connection status"),
    ],
    "buddy": [
        ("on",     "Enable AI companion"),
        ("off",    "Disable AI companion"),
        ("pet",    "Interact with your companion"),
        ("switch", "Switch companion character"),
    ],
    "passes": [
        ("list",  "List your passes"),
        ("share", "Share a free week with a friend"),
    ],
    "pr": [
        ("create",   "Draft and create a pull request"),
        ("status",   "Show open PRs"),
        ("comments", "Show PR review comments"),
    ],
    "branch": [
        ("list",   "List all branches"),
        ("create", "Create a new branch"),
        ("switch", "Switch to a branch"),
        ("delete", "Delete a branch"),
        ("recent", "Show recently used branches"),
    ],
    "worktree": [
        ("list",   "List git worktrees"),
        ("add",    "Add a new worktree"),
        ("remove", "Remove a worktree"),
    ],
    "session": [
        ("list",   "List all saved sessions"),
        ("new",    "Start a new session"),
        ("info",   "Show current session info"),
        ("save",   "Save current session as named"),
        ("delete", "Delete a saved session"),
    ],
    "memory": [
        ("show",   "Show memory contents"),
        ("search", "Search memories by query"),
        ("stats",  "Memory statistics and health"),
        ("edit",   "Edit a memory entry"),
    ],
    "mcp": [
        ("list",      "List connected MCP servers"),
        ("reconnect", "Reconnect to an MCP server"),
        ("health",    "Check MCP server health"),
    ],
    "agents": [
        ("list",   "List running agents"),
        ("create", "Create a named agent"),
        ("info",   "Show agent details"),
        ("delete", "Remove an agent"),
        ("reload", "Reload agent definitions"),
    ],
    "task": [
        ("create", "Create a new task"),
        ("list",   "List all tasks"),
        ("view",   "View a task's details"),
        ("update", "Update a task"),
        ("done",   "Mark a task as completed"),
    ],
    "todo": [
        ("add",   "Add a new todo item"),
        ("list",  "List all todos"),
        ("clear", "Clear completed todos"),
    ],
    "budget": [
        ("limit", "Show current budget limit"),
        ("set",   "Set a new spending limit"),
    ],
    "chrome": [
        ("status",  "Check Chrome extension status"),
        ("install", "Install Chrome extension"),
    ],
    "remote-env": [
        ("show", "Show remote environment config"),
        ("set",  "Set a remote environment variable"),
    ],
    "shutdown": [
        ("cancel", "Cancel a pending shutdown"),
    ],
}


# Multi-step command flows: list of steps, each is {"type": "pick"|"input", ...}
# Add "optional": True to a step to allow Esc/cancel without aborting the whole flow.
_COMMAND_FLOWS: dict[str, list] = {
    "agent": [
        {"type": "pick", "title": "Select agent type", "options": [
            ("scout",            "Read-only explorer"),
            ("worker",           "Full access — read, write, run"),
            ("planner",          "Analysis and planning only"),
            ("reviewer",         "Code review specialist"),
            ("security-auditor", "Security analysis"),
        ]},
        {"type": "input", "title": "Agent task", "placeholder": "Describe the task…"},
    ],
    "spawn": [
        {"type": "pick", "title": "Select agent type", "options": [
            ("scout",            "Read-only explorer"),
            ("worker",           "Full access — read, write, run"),
            ("planner",          "Analysis and planning only"),
            ("reviewer",         "Code review specialist"),
            ("security-auditor", "Security analysis"),
        ]},
        {"type": "input", "title": "Background task", "placeholder": "Describe the task (runs non-blocking)…"},
    ],
    "delegate": [
        {"type": "input", "title": "Task to delegate", "placeholder": "Describe what you need done…"},
        {"type": "pick", "title": "Specialist agent (Esc to auto-select)", "optional": True, "options": [
            ("terminal",  "Terminal/shell operations"),
            ("network",   "Network and web tasks"),
            ("security",  "Security analysis"),
            ("file",      "File system operations"),
            ("desktop",   "Desktop/GUI automation"),
            ("app",       "Application management"),
            ("system",    "System administration"),
            ("vision",    "Computer vision tasks"),
            ("research",  "Research and analysis"),
        ]},
    ],
    "orchestrate": [
        {"type": "input", "title": "Orchestrate multi-agent pipeline", "placeholder": "Describe the goal…"},
    ],
    "coordinate": [
        {"type": "input", "title": "Coordinate parallel agents", "placeholder": "Describe the task to decompose…"},
    ],
    "swarm": [
        {"type": "input", "title": "Spawn agent swarm", "placeholder": "Describe the task to decompose…"},
    ],
}

# Commands that show a fzf text-input prompt before dispatching
_COMMAND_PROMPTS: dict[str, dict] = {
    "add-dir": {
        "title": "Add directory to workspace",
        "desc":  "JARVIS will be able to read and edit files in this directory.",
        "placeholder": "Directory path…",
        "path": True,
    },
    "worker": {
        "title": "Spawn worker agent",
        "desc":  "Full-access agent that can read, write, and run commands.",
        "placeholder": "Describe the task…",
        "path": False,
    },
    "scout": {
        "title": "Spawn scout agent",
        "desc":  "Read-only agent for exploration and research.",
        "placeholder": "Describe the task…",
        "path": False,
    },
    "planner": {
        "title": "Spawn planner agent",
        "desc":  "Analysis-only agent that produces structured plans.",
        "placeholder": "Describe what to plan…",
        "path": False,
    },
    "learn": {
        "title": "Store a fact in memory",
        "desc":  "Saved to the Neural Lattice for future recall.",
        "placeholder": "Enter a fact to remember…",
        "path": False,
    },
    "recall": {
        "title": "Search memory",
        "desc":  "Search the Neural Lattice for relevant memories.",
        "placeholder": "What do you want to recall?…",
        "path": False,
    },
    "forget": {
        "title": "Remove a memory",
        "desc":  "Delete a memory node by ID, query, or filename.",
        "placeholder": "Memory ID, query or filename…",
        "path": False,
    },
    "associations": {
        "title": "Explore memory associations",
        "desc":  "Show connected memories for a concept.",
        "placeholder": "Concept to explore…",
        "path": False,
    },
    "common-sense": {
        "title": "Common-sense knowledge query",
        "desc":  "Query the common-sense knowledge base.",
        "placeholder": "Ask a common-sense question…",
        "path": False,
    },
    "recon": {
        "title": "Reconnaissance target",
        "desc":  "Full recon: whois, DNS, nmap, gobuster (DANGEROUS on external hosts).",
        "placeholder": "Target host or IP…",
        "path": False,
    },
    "pentest": {
        "title": "Penetration test target",
        "desc":  "Automated pentest workflow — ONLY on systems you own/have permission.",
        "placeholder": "Target host or IP…",
        "path": False,
    },
    "mcp-disconnect": {
        "title": "Disconnect MCP server",
        "desc":  "Enter the MCP server name to disconnect.",
        "placeholder": "Server name…",
        "path": False,
    },
    "kill-agent": {
        "title": "Kill agent by ID",
        "desc":  "Stop a running agent.",
        "placeholder": "Agent ID…",
        "path": False,
    },
    "rename": {
        "title": "Rename session",
        "desc":  "Give the current session a new name.",
        "placeholder": "New session name…",
        "path": False,
    },
    "feedback": {
        "title": "Submit feedback",
        "desc":  "Your feedback is stored locally in ~/.jarvis/feedback/.",
        "placeholder": "Your feedback…",
        "path": False,
    },
    "btw": {
        "title": "Side question",
        "desc":  "Ask a quick question without interrupting the main conversation.",
        "placeholder": "Your side question…",
        "path": False,
    },
    "explain": {
        "title": "Explain code or file",
        "desc":  "JARVIS will read and explain the code at the given path.",
        "placeholder": "File path or code snippet…",
        "path": True,
    },
    "team": {
        "title": "Spawn a team for a goal",
        "desc":  "JARVIS will create a multi-agent team to accomplish this goal.",
        "placeholder": "Describe the goal…",
        "path": False,
    },
    "ultraplan": {
        "title": "Deep planning with research",
        "desc":  "Scout + planner agents work together to produce a detailed plan.",
        "placeholder": "Describe the goal…",
        "path": False,
    },
    "fix-error": {
        "title": "Fix a runtime or syntax error",
        "desc":  "Paste a traceback or describe the error.",
        "placeholder": "Error description or traceback…",
        "path": False,
    },
    "rpc": {
        "title": "Call MCP tool directly",
        "desc":  "Format: tool_name {\"arg\": \"value\"}",
        "placeholder": "tool_name {\"args\"}…",
        "path": False,
    },
    "tag": {
        "title": "Tag session",
        "desc":  "Format: add <tag>  or  remove <tag>  or  list",
        "placeholder": "add <tag> / remove <tag> / list",
        "path": False,
    },
    "alias": {
        "title": "Create command alias",
        "desc":  "Format: <alias_name> <command>",
        "placeholder": "myalias /some-command args…",
        "path": False,
    },
    "tool-search": {
        "title": "Search tools",
        "desc":  "Search built-in and MCP tools by name or description.",
        "placeholder": "Search query…",
        "path": False,
    },
    "import": {
        "title": "Import session from file",
        "desc":  "Provide a path to a session export file.",
        "placeholder": "File path…",
        "path": True,
    },
    "install": {
        "title": "Install plugin or skill",
        "desc":  "Provide the path to the plugin/skill file.",
        "placeholder": "File path…",
        "path": True,
    },
    "uninstall": {
        "title": "Uninstall plugin or skill",
        "desc":  "Enter the plugin or skill name to remove.",
        "placeholder": "Plugin or skill name…",
        "path": False,
    },
    "skill": {
        "title": "View skill details",
        "desc":  "Enter the skill name to inspect.",
        "placeholder": "Skill name…",
        "path": False,
    },
    "plugin": {
        "title": "Manage plugin",
        "desc":  "Format: install|enable|disable|remove <name>",
        "placeholder": "install|enable|disable|remove <name>…",
        "path": False,
    },
    "apply-fix": {
        "title": "Apply a fix by number",
        "desc":  "Enter the fix number from /troubleshoot output.",
        "placeholder": "Fix number (e.g. 1)…",
        "path": False,
    },
    "wake": {
        "title": "Wake-on-LAN",
        "desc":  "Send a magic packet to wake a sleeping machine.",
        "placeholder": "MAC address (e.g. AA:BB:CC:DD:EE:FF)…",
        "path": False,
    },
}


async def _fzf(args: list, input_text: str = "") -> str:
    """Run fzf in a thread so the async event loop stays alive (keeps WS alive)."""
    import asyncio as _asyncio
    loop = _asyncio.get_event_loop()
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(args, input=input_text, text=True, stdout=subprocess.PIPE),
    )
    return proc.stdout.strip() if proc and proc.returncode == 0 else ""


async def _fetch_model_entries(client) -> list[tuple]:
    """Fetch all available models. Returns list of (label, provider, model_name, is_active).

    Scans:
    - All configured providers (cloud + any registered Ollama remotes)
    - Live /api/tags from every configured Ollama endpoint
    - localhost:11434 as final fallback
    """
    entries = []
    try:
        import urllib.request as _ur, json as _j
        from src.reasoning.providers import ProviderRegistry
        reg = ProviderRegistry()

        # Configured cloud/remote providers
        for p in reg.get_active_providers():
            is_local = "localhost" in p.base_url or "127.0.0.1" in p.base_url
            tag = "local" if is_local else "cloud"
            for m in p.models:
                entries.append((f"{m}  [{tag}]", p.name, m, False))

        # Live Ollama model list from all configured Ollama endpoints + localhost fallback
        _seen_urls: set[str] = set()
        _seen_models: set[str] = {e[2] for e in entries}
        _ollama_endpoints: list[tuple[str, str]] = []
        for p in reg.get_active_providers():
            if "11434" in p.base_url or "ollama" in p.name.lower():
                _base = p.base_url.rstrip("/")
                if _base.endswith("/v1"):
                    _base = _base[:-3]
                _ollama_endpoints.append((_base, p.name))
        _ollama_endpoints.append(("http://localhost:11434", "ollama"))  # always try local

        for _ourl, _opname in _ollama_endpoints:
            if _ourl in _seen_urls:
                continue
            _seen_urls.add(_ourl)
            try:
                _resp = _ur.urlopen(f"{_ourl}/api/tags", timeout=2)
                _omodels = [m["name"] for m in _j.loads(_resp.read()).get("models", [])]
                _is_local = "localhost" in _ourl or "127.0.0.1" in _ourl
                _tag = "local/ollama" if _is_local else f"remote/{_opname}"
                for _m in _omodels:
                    if _m not in _seen_models:
                        entries.append((f"{_m}  [{_tag}]", _opname, _m, False))
                        _seen_models.add(_m)
            except Exception:
                pass
    except Exception:
        pass
    return entries


async def _interactive_pick(entries: list[str], title: str = "", current: int = 0) -> int | None:
    """Arrow-key interactive list picker. Returns selected index or None on Esc/q."""
    import tty, termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    sel = max(0, min(current, len(entries) - 1))
    MAX_VIS = 10

    def _render():
        # Clear all lines we'll use
        total = len(entries)
        rows = min(MAX_VIS, total)
        header_lines = 2 if title else 1
        # Move cursor up to top of our block if already drawn
        sys.stdout.write("\033[2K\r")
        if title:
            sys.stdout.write(f"  {DIM}{title}{RESET}\n\033[2K\r\n")
        start = max(0, min(sel - MAX_VIS // 2, total - MAX_VIS))
        end = min(total, start + MAX_VIS)
        if start > 0:
            sys.stdout.write(f"  {DIM}↑ {start} more{RESET}\n\033[2K\r")
        for i in range(start, end):
            pfx = f"  {CYAN}❯{RESET} " if i == sel else "    "
            sys.stdout.write(f"\033[2K\r{pfx}{entries[i]}\n")
        if end < total:
            sys.stdout.write(f"\033[2K\r  {DIM}↓ {total - end} more{RESET}\n")
        sys.stdout.flush()

    try:
        tty.setraw(fd)
        _render()
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                nxt = sys.stdin.read(2)
                if nxt == "[A":  # up
                    sel = max(0, sel - 1)
                elif nxt == "[B":  # down
                    sel = min(len(entries) - 1, sel + 1)
                else:
                    return None
            elif ch in ("\r", "\n"):
                return sel
            elif ch in ("q", "\x03"):
                return None
            # Redraw: move cursor back up
            total = len(entries)
            vis = min(MAX_VIS, total)
            extra = (1 if sel > 0 else 0) + (1 if sel < total - 1 else 0)
            header_lines = 2 if title else 1
            lines_drawn = vis + extra + header_lines
            sys.stdout.write(f"\033[{lines_drawn}A")
            _render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


async def main(args: types.SimpleNamespace):
    # CLI runs as the owner — no sandbox, full permissions
    os.environ.setdefault("JARVIS_NO_SANDBOX", "1")
    os.environ.setdefault("JARVIS_OWNER", "ulrich")

    # Suppress asyncio "Cannot write to closing transport" and similar shutdown noise
    def _quiet_exception_handler(loop, context):
        msg = context.get("message", "")
        if "closing transport" in msg or "connection lost" in msg.lower():
            return  # harmless shutdown race, ignore
        loop.default_exception_handler(context)
    asyncio.get_event_loop().set_exception_handler(_quiet_exception_handler)

    # MCP server mode
    if args.serve:
        from src.mcp.server import MCPServer
        server = MCPServer()
        await server.run()
        return

    # Handle piped stdin
    stdin_data = ""
    if not sys.stdin.isatty():
        stdin_data = sys.stdin.read()
        if not args.print_mode:
            try:
                sys.stdin = open("/dev/tty", "r")
            except OSError:
                pass

    # Suppress startup logs in CLI mode
    import logging
    logging.getLogger("jarvis").setLevel(logging.WARNING)
    logging.getLogger("src").setLevel(logging.WARNING)
    logging.getLogger("groq").setLevel(logging.WARNING)

    # Session manager
    from src.sessions import SessionManager
    session_mgr = SessionManager()

    # Start brain (silently)
    client = StandaloneBrain()
    if not await client.connect():
        _writeln(f"  {RED}Failed to start. Check your API keys in .env{RESET}")
        return

    brain = client.brain

    # In server mode, brain is None — create a proxy that routes LLM calls to server.
    class _BrainProxy:
        """Thin brain proxy for server mode. Routes think() to the remote server."""
        mode = "normal"
        _pending_fixes = []
        _companion = None
        _fast_mode = False

        def __init__(self, _client):
            self._client = _client
            # Minimal reasoner stub that routes messages to server
            class _Reasoner:
                def __init__(self, c):
                    self._c = c
                    self.providers = type("P", (), {"get_active_providers": lambda s: []})()
                async def query(self, messages):
                    user_msg = next(
                        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
                    )
                    return await self._c.query(user_msg)
                async def chat(self, messages):
                    return await self.query(messages)
            self.reasoner = _Reasoner(_client)
            # Minimal permissions stub
            self.permissions = type("Perms", (), {"level": 2})()
            # Minimal memory stub
            self.memory = type("Mem", (), {
                "add_turn": lambda self, *a, **kw: None,
                "recall_as_context": lambda self, *a, **kw: "",
            })()

        async def think(self, msg: str, **kwargs) -> str:
            return await self._client.query(msg)

        async def think_stream(self, msg: str, **kwargs):
            result = await self._client.query(msg)
            yield result

        def dispatch_command(self, *a, **kw):
            return None

    if brain is None:
        brain = _BrainProxy(client)

    # ── Wire up new CLI flags ──
    _verbose = False

    # Model alias mapping
    _MODEL_ALIASES = {
        "opus": "claude-opus-4-6-20250514",
        "sonnet": "claude-sonnet-4-6-20250514",
        "haiku": "claude-haiku-4-5-20251001",
    }

    if args.model and client._is_full_brain:
        resolved = _MODEL_ALIASES.get(args.model.lower(), args.model)
        try:
            providers = brain.reasoner.providers.get_active_providers()
            if providers:
                providers[0].model = resolved
        except Exception:
            pass

    if args.effort and client._is_full_brain:
        try:
            brain.reasoner.effort = args.effort
        except Exception:
            pass

    # Apply --theme flag (before any rendering)
    if args.theme:
        _apply_theme(args.theme)

    if args.bare and client._is_full_brain:
        # Skip hooks, plugins, MCP discovery in bare mode
        try:
            if hasattr(brain, "hooks"):
                brain.hooks.rules = []
            if hasattr(brain, "plugins"):
                brain.plugins.plugins = []
            if hasattr(brain, "mcp"):
                brain.mcp.servers = {}
        except Exception:
            pass

    if args.permission_mode and client._is_full_brain:
        try:
            if hasattr(brain, "permissions"):
                brain.permissions.level = args.permission_mode
        except Exception:
            pass

    if args.dangerously_skip_permissions and client._is_full_brain:
        try:
            if hasattr(brain, "permissions"):
                brain.permissions.level = "bypass"
        except Exception:
            pass

    if args.verbose:
        _verbose = True

    if args.debug:
        logging.getLogger("jarvis").setLevel(logging.DEBUG)
        logging.getLogger("src").setLevel(logging.DEBUG)
        if args.debug != "all":
            for filt in args.debug.split(","):
                logging.getLogger(f"brain.{filt.strip()}").setLevel(logging.DEBUG)

    # System prompt handling
    _custom_system_prompt = None
    if args.system_prompt:
        _custom_system_prompt = args.system_prompt
    elif args.system_prompt_file:
        _custom_system_prompt = args.system_prompt_file

    if _custom_system_prompt and client._is_full_brain:
        try:
            brain.reasoner.system_prompt = _custom_system_prompt
        except Exception:
            pass

    if args.append_system_prompt and client._is_full_brain:
        try:
            existing = getattr(brain.reasoner, "system_prompt", "") or ""
            brain.reasoner.system_prompt = existing + "\n" + args.append_system_prompt
        except Exception:
            pass

    if args.thinking and client._is_full_brain:
        try:
            brain.reasoner.thinking_mode = args.thinking
        except Exception:
            pass

    if args.max_turns and client._is_full_brain:
        try:
            brain.agent_max_turns = args.max_turns
        except Exception:
            pass

    if args.mcp_config and client._is_full_brain:
        try:
            if hasattr(brain, "mcp"):
                brain.mcp.load_config(args.mcp_config)
        except Exception:
            pass

    if args.allowed_tools and client._is_full_brain:
        try:
            brain.tool_allowlist = set(args.allowed_tools)
        except Exception:
            pass

    if args.disallowed_tools and client._is_full_brain:
        try:
            brain.tool_denylist = set(args.disallowed_tools)
        except Exception:
            pass

    # Session management
    if args.continue_last:
        session = session_mgr.get_latest()
        if session:
            session_mgr.resume(session)
            if client._is_full_brain and session.mode:
                brain.mode = session.mode
        else:
            session_mgr.new(name=args.name, mode=args.mode)
    elif args.resume:
        session = session_mgr.find(args.resume)
        if session:
            session_mgr.resume(session)
            if client._is_full_brain and session.mode:
                brain.mode = session.mode
        else:
            _writeln(f"  {RED}Session not found: {args.resume}{RESET}")
            session_mgr.new(name=args.name, mode=args.mode)
    else:
        session_mgr.new(name=args.name, mode=args.mode)

    # Set initial mode
    if client._is_full_brain and args.mode != "normal":
        brain.mode = args.mode

    # Print mode (one-shot) — uses think_stream for full tool access
    if args.print_mode:
        import json as _json
        query = args.print_mode
        if stdin_data:
            query = f"{query}\n\n{stdin_data}"

        # Intercept slash commands in print mode — always use local brain
        if query.startswith("/"):
            cmd_parts = query[1:].split(None, 1)
            cmd_name = cmd_parts[0] if cmd_parts else ""
            cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            # Ensure we have a local brain for command dispatch
            if brain is None:
                from src.brain import Brain as _Brain
                brain = _Brain()
            result = await brain.dispatch_command(cmd_name, cmd_args, session_mgr=session_mgr)
            if result and result.text:
                print(result.text)
            elif result and not result.success:
                print(f"Command error: {result.text or 'unknown error'}")
            await client.close()
            session_mgr.close()
            return

        session_mgr.add_message("user", query)
        output_fmt = getattr(args, "output_format", "text")
        full_response = ""
        tool_calls_log = []
        usage_info = {}
        async for event in client.query_stream(query):
            t = event.get("type", "")
            if t == "text":
                chunk = event.get("content", "")
                full_response += chunk
                if output_fmt == "text":
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                elif output_fmt == "stream-json":
                    sys.stdout.write(_json.dumps({"type": "text", "content": chunk}) + "\n")
                    sys.stdout.flush()
            elif t == "tool_call":
                name = event.get("name", "")
                tc_entry = {"name": name, "args": event.get("args", {})}
                tool_calls_log.append(tc_entry)
                if output_fmt == "text":
                    sys.stderr.write(f"  {name}\n")
                elif output_fmt == "stream-json":
                    sys.stdout.write(_json.dumps({"type": "tool_call", **tc_entry}) + "\n")
                    sys.stdout.flush()
            elif t == "tool_result":
                if output_fmt == "stream-json":
                    sys.stdout.write(_json.dumps({
                        "type": "tool_result",
                        "name": event.get("name", ""),
                        "result": event.get("result", ""),
                    }) + "\n")
                    sys.stdout.flush()
            elif t == "usage":
                usage_info = {k: v for k, v in event.items() if k != "type"}
                if output_fmt == "stream-json":
                    sys.stdout.write(_json.dumps({"type": "usage", **usage_info}) + "\n")
                    sys.stdout.flush()
            elif t == "done":
                if output_fmt == "stream-json":
                    sys.stdout.write(_json.dumps({"type": "done"}) + "\n")
                    sys.stdout.flush()
                break
        if output_fmt == "text":
            if full_response:
                print()
        elif output_fmt == "json":
            result = {"response": full_response, "tool_calls": tool_calls_log, "usage": usage_info}
            sys.stdout.write(_json.dumps(result, indent=2) + "\n")
            sys.stdout.flush()
        session_mgr.add_message("jarvis", full_response)
        session_mgr.save_current()
        await client.close()
        session_mgr.close()
        return

    # ── Banner ──
    def render_banner(model, provider, cwd, session_name, cmd_count):
        """JARVIS banner — arc reactor logo left, info right."""
        SILVER = "\033[38;5;250m"
        SILVER_BRIGHT = "\033[38;5;255m"
        # Arc reactor mark
        mascot = [
            f"{SILVER}  ╔═◈═╗  {RESET}",
            f"{SILVER_BRIGHT}  ║ ◉ ║  {RESET}",
            f"{SILVER}  ╚═◈═╝  {RESET}",
        ]
        # Info lines
        info = [
            f"{BOLD}{SILVER_BRIGHT}JARVIS v2.0{RESET}",
            f"{SILVER}{model} · {provider}{RESET}",
            f"{DIM}ready{RESET}",
        ]
        lines = []
        for i in range(len(mascot)):
            lines.append(f"{mascot[i]}  {info[i] if i < len(info) else ''}")
        return "\n" + "\n".join(lines) + "\n"

    # Determine banner values
    # Shorten CWD with ~ for home directory
    cwd_display = os.getcwd().replace(os.path.expanduser("~"), "~")
    session_name = ""
    if session_mgr.current and session_mgr.current.name:
        session_name = session_mgr.current.name
    else:
        session_name = "new session"

    # Get model/provider info
    model_name = "local"
    provider_name = "local"
    if client._server_mode:
        # Show the remote server host so it's obvious which brain is being used
        import re as _re
        _host = _re.sub(r'^https?://', '', client._server_url).rstrip('/')
        model_name = "remote"
        provider_name = _host
        # Try to get actual model name from server
        try:
            import urllib.request, json as _j
            resp = urllib.request.urlopen(f"{client._server_url}/api/providers", timeout=2)
            data = _j.loads(resp.read())
            provs = data.get("providers", [])
            if provs:
                model_name = provs[0].get("model", "remote")
                provider_name = f"{provs[0].get('name', _host)} @ {_host}"
        except Exception:
            pass
    elif brain and hasattr(brain, "reasoner"):
        try:
            providers = brain.reasoner.providers.get_active_providers()
            if providers:
                p = providers[0]
                model_name = p.model or "local"
                provider_name = p.name or "local"
        except Exception:
            pass

    cmd_count = 91
    try:
        from src.commands import registry as cmd_registry
        cmd_count = cmd_registry.visible_count
    except Exception:
        pass

    # ── Workspace Trust Prompt (like JARVIS) ──
    trust_file = os.path.join(os.path.expanduser("~"), ".jarvis", "trusted_dirs.json")
    cwd = os.getcwd()

    def _is_trusted(directory):
        """Check if this directory has been trusted before."""
        try:
            import json as _json
            if os.path.exists(trust_file):
                with open(trust_file) as f:
                    trusted = _json.loads(f.read())
                return directory in trusted
        except (OSError, ValueError):
            pass
        return False

    def _trust_dir(directory):
        """Mark a directory as trusted.
        Config path follows XDG via typer.get_app_dir('jarvis') on all platforms.
        We use ~/.jarvis for backward compat but fall back to get_app_dir if missing.
        """
        import json as _json
        _alt_dir = typer.get_app_dir("jarvis")  # platform-aware: ~/.config/jarvis on Linux
        trusted = []
        try:
            if os.path.exists(trust_file):
                with open(trust_file) as f:
                    trusted = _json.loads(f.read())
        except (OSError, ValueError):
            pass
        if directory not in trusted:
            trusted.append(directory)
        os.makedirs(os.path.dirname(trust_file), exist_ok=True)
        with open(trust_file, "w") as f:
            f.write(_json.dumps(trusted, indent=2))

    if not _is_trusted(cwd):
        tw = 80
        try:
            tw = os.get_terminal_size().columns
        except OSError:
            pass
        _writeln()
        _writeln(f"{DIM}{'─' * tw}{RESET}")
        _writeln(f" Accessing workspace:")
        _writeln()
        _writeln(f" {BOLD}{cwd}{RESET}")
        _writeln()
        _writeln(f" {DIM}JARVIS will be able to read, edit, and execute files here.{RESET}")
        _writeln()
        _writeln()
        try:
            trusted = typer.confirm(f" Trust this directory?", default=True)
        except (EOFError, KeyboardInterrupt):
            trusted = False
        if not trusted:
            typer.echo(typer.style("  Exiting. Run jarvis from a trusted directory.", fg=typer.colors.BRIGHT_BLACK))
            raise typer.Exit()
        _trust_dir(cwd)
        _writeln()

    # Auto-detect terminal: VS Code integrated terminal has no alt-screen scrollback,
    # so use normal screen there. Real terminals (kitty, alacritty, gnome-terminal, etc.)
    # support alt screen scrollback — use it for full session isolation.
    _in_vscode = (
        os.environ.get("TERM_PROGRAM") == "vscode"
        or "VSCODE_INJECTION" in os.environ
        or "VSCODE_GIT_IPC_HANDLE" in os.environ
    )

    if sys.stdout.isatty():
        if _in_vscode:
            # VS Code: normal screen + clear scrollback so scroll stays within this session.
            sys.stdout.write("\033[3J\033[H\033[2J")
        else:
            # Real terminal: alt screen gives isolated scrollback, clean exit restores shell.
            sys.stdout.write("\033[?1049h\033[H\033[2J")
        sys.stdout.flush()

    def _exit_alt_screen():
        if not _in_vscode and sys.stdout.isatty():
            sys.stdout.write("\033[?1049l")
            sys.stdout.flush()

    def _tw():
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80

    banner = render_banner(model_name, provider_name, cwd_display, session_name, cmd_count)
    _writeln(banner)

    # Startup tip — uses the tip service for cooldown-aware scheduling
    # Falls back to a random built-in tip if the service fails
    _tip_text = None
    try:
        from src.services.tips.tipScheduler import get_tip_to_show_on_spinner, record_shown_tip
        import asyncio as _tip_asyncio
        _loop = _tip_asyncio.get_event_loop()
        if _loop.is_running():
            # We're already in an async context — schedule it
            import concurrent.futures
            _tip_fut = concurrent.futures.Future()
            async def _get_tip():
                try:
                    tip = await get_tip_to_show_on_spinner()
                    if tip:
                        content = tip.content
                        text = await content() if _tip_asyncio.iscoroutinefunction(content) else content()
                        record_shown_tip(tip)
                        _tip_fut.set_result(text)
                    else:
                        _tip_fut.set_result(None)
                except Exception:
                    _tip_fut.set_result(None)
            _tip_asyncio.ensure_future(_get_tip())
            # Don't block — use fallback if not ready
        else:
            _selected_tip = _loop.run_until_complete(get_tip_to_show_on_spinner())
            if _selected_tip:
                content = _selected_tip.content
                _tip_text = _loop.run_until_complete(content()) if _tip_asyncio.iscoroutinefunction(content) else content()
                record_shown_tip(_selected_tip)
    except Exception:
        pass

    if not _tip_text:
        import random
        _fallback_tips = [
            "Type / to see all commands",
            "Use arrow keys to browse history",
            "Ctrl+R to search history",
            "Try /ultraplan for complex tasks",
            "/copy grabs the last code block",
            "!cmd runs a shell command inline",
            "!!cmd runs and analyzes the output",
            "/doctor checks your setup",
        ]
        _tip_text = random.choice(_fallback_tips)
    _writeln(f"  {DIM}tip: {_tip_text}{RESET}")
    _writeln()

    # Initialize companion
    from src.cli.companion import Companion
    _companion = Companion()
    if brain is not None:
        brain._companion = _companion

    def _buddy_says(context: str):
        """Show companion comment if enabled and off cooldown."""
        if not _companion.enabled:
            return
        comment = _companion.get_comment(context)
        if comment:
            _outputln(_companion.render_comment(comment))

    # Resume context
    if (args.continue_last or args.resume) and session_mgr.current:
        s = session_mgr.current
        _writeln(f"  {GREEN}Resumed:{RESET} {s.display_name} ({s.turn_count} turns)")
        recent = [m for m in s.messages[-4:] if m["role"] in ("user", "jarvis")]
        if recent:
            for m in recent:
                role = f"{CYAN}you{RESET}" if m["role"] == "user" else f"{GREEN}jarvis{RESET}"
                preview = m["content"][:80].replace("\n", " ")
                _writeln(f"    {role}: {DIM}{preview}{'...' if len(m['content']) > 80 else ''}{RESET}")
        _writeln()

    # Initial query from args
    initial_query = " ".join(args.query) if args.query else ""
    if stdin_data and initial_query:
        initial_query = f"{initial_query}\n\n{stdin_data}"
    elif stdin_data:
        initial_query = f"Analyze this:\n\n{stdin_data}"

    spinner = Spinner()
    _cancelled = False
    _active_task: asyncio.Task | None = None
    _input_queue: asyncio.Queue = asyncio.Queue()

    # ── Vim Mode State (src/vim) ──
    _vim_state: VimState = create_initial_vim_state()
    _vim_persistent: PersistentState = create_initial_persistent_state()
    _vim_enabled = False  # Enable with /vim command or --vim flag

    def _vim_handle_normal_key(key: str, buf: list) -> bool:
        """Handle a keypress in vim NORMAL mode. Returns True if handled."""
        nonlocal _vim_state, _vim_persistent
        if not isinstance(_vim_state, NormalState):
            return False
        text = "".join(buf)

        # Simple motions
        if key in SIMPLE_MOTIONS:
            # Just move cursor conceptually (in single-line input, h/l are most useful)
            return True

        # Mode transitions
        if key == "i":
            _vim_state = enter_insert(_vim_state)
            return True
        if key == "a":
            _vim_state = enter_insert(_vim_state)
            return True
        if key == "A":
            _vim_state = enter_insert(_vim_state)
            return True
        if key == "I":
            _vim_state = enter_insert(_vim_state)
            return True

        # Delete line: dd
        if key == "d" and hasattr(_vim_state.command, 'op') and _vim_state.command.type == "operator" and _vim_state.command.op == "delete":
            deleted = text
            buf.clear()
            _vim_persistent.register = deleted
            _vim_state = NormalState(command=IdleCommand())
            return True

        # Operators
        if key in OPERATORS:
            from src.vim.types import OperatorCommand
            _vim_state = NormalState(command=OperatorCommand(op=OPERATORS[key], count=1))
            return True

        # Paste from register
        if key == "p" and _vim_persistent.register:
            buf.extend(_vim_persistent.register)
            return True

        # Undo (u) - clear buffer
        if key == "u":
            buf.clear()
            return True

        return False

    # ── Inline terminal layout (dynamic — frame sits right below output) ─────
    # Frame layout (4 rows drawn inline after output):
    #   top separator  ─────────────────────────────
    #   prompt line    ❯ <input text>
    #   foot separator ─────────────────────────────
    #   footer         ? for shortcuts
    #
    # The frame is always drawn immediately below the last output line.
    # It moves down as content grows — no fixed empty gap.
    #
    # No-blink guarantee: _output() and _outputln() erase the frame, write
    # the text, and redraw the frame in one buffered sequence before flushing.
    # The terminal sees an atomic update — erase+write+redraw all land at once.

    _frame_drawn = False
    _ANSI_ESCAPE_RE = re.compile(r'\033\[[^m]*m')
    _last_was_question = [False]   # True when JARVIS last response ended with '?'

    def _setup_zones():
        pass   # no scroll region needed for inline layout

    def _teardown_zones():
        pass

    def _build_frame_parts():
        """Return (sep, prompt, footer) strings."""
        tw = _tw()
        mode_str = brain.mode if client._is_full_brain else "normal"
        right_parts = []
        if model_name and model_name != "local":
            right_parts.append(model_name)
        if mode_str and mode_str != "normal":
            right_parts.append(mode_str)
        try:
            effort_val = (brain._effort_level if client._is_full_brain and hasattr(brain, '_effort_level') else None) or "high"
            right_parts.append(f"/effort · {effort_val}")
        except Exception:
            pass
        # Cost removed from footer — use /cost for per-provider breakdown
        if _companion and _companion.enabled and hasattr(_companion, 'data') and _companion.data:
            cname = _companion.data.get("name", "")
            if cname:
                right_parts.append(cname)
        right_str = " · ".join(right_parts)
        sep = f"{DIM}{'─' * tw}{RESET}"
        vim_indicator = ""
        if _vim_enabled:
            vim_indicator = f"{BLUE}[N]{RESET} " if isinstance(_vim_state, NormalState) else f"{GREEN}[I]{RESET} "
        mode_str2 = brain.mode if client._is_full_brain else "normal"
        prompt = (f"{vim_indicator}{YELLOW}{mode_str2}{RESET} ❯ "
                  if mode_str2 != "normal" else f"{vim_indicator}❯ ")
        if _last_was_question[0]:
            left = f"  {DIM}[y] yes  [n] no  · any other key to type freely{RESET}"
        else:
            left = f"  {DIM}? for shortcuts{RESET}"
        if right_str:
            vl = _display_width(_ANSI_ESCAPE_RE.sub('', left))
            vr = _display_width(right_str)
            available = tw - vl - 2  # minimum 1 space separator
            if vr > available:
                # Terminal too narrow — drop right_parts one by one from left until it fits
                trimmed = list(right_parts)
                while trimmed and _display_width(" · ".join(trimmed)) > available:
                    trimmed.pop(0)
                right_str = " · ".join(trimmed)
                vr = _display_width(right_str)
            if right_str:
                pad = max(1, tw - vl - vr - 2)
                footer = f"{left}{' ' * pad}{DIM}{right_str}{RESET}"
            else:
                footer = left
        else:
            footer = left
        return sep, prompt, footer

    # Shared mutable spinner state — must live in main() scope so that
    # _erase_frame() (also in main() scope) can clear the spinner line.
    _spin_line_active = [False]   # True while the spinner line is visible above the frame
    _spin_task_ref = [None]       # current asyncio Task, or None

    def _draw_input_frame(mode_prefix="", buf_text=""):
        """Draw the 4-row inline frame. Layout: top-sep | prompt | bot-sep | footer.
        Always erases the existing frame first."""
        nonlocal _frame_drawn
        _erase_frame()            # cursor → frame start, clears to end of screen
        sep, prompt, footer = _build_frame_parts()
        _write("\033[?25l")       # hide cursor during redraw
        _write("\n")              # blank line separates output from frame
        _write(sep + "\n")
        _write(f"{prompt}\033[0;1;97m{buf_text}\033[0m\n")
        _write(sep + "\n")
        _write(footer)
        # Cursor is at footer. Go up 2 rows to prompt line.
        _write("\033[2A")
        _prompt_vis_len = _display_width(_ANSI_ESCAPE_RE.sub('', prompt))
        _target_col = _prompt_vis_len + _display_width(buf_text)
        _write(f"\r\033[{_target_col}C" if _target_col > 0 else "\r")
        _write("\033[?25h")
        _frame_drawn = True
        sys.stdout.flush()

    def _erase_frame():
        """Erase the inline frame (and spinner line if active).
        Cursor lands where the frame started."""
        nonlocal _frame_drawn
        if not _frame_drawn:
            return
        if _spin_line_active[0]:
            # Spinner line sits 1 row above the blank-line separator (3 rows above
            # the prompt).  Cancel the task and clear it together with the frame.
            t = _spin_task_ref[0]
            if t and not t.done():
                t.cancel()
            _spin_task_ref[0] = None
            _spin_line_active[0] = False
            _write("\033[3A\r\033[J")   # 3 up → spinner line, clear to end
        else:
            _write("\033[2A\r\033[J")   # 2 up → blank line, clear to end
        _frame_drawn = False

    _output_buf_text = [""]  # current input text, kept for spinner redraws

    def _output(text: str):
        """Write text atomically: erase frame → text → redraw frame in one flush."""
        _erase_frame()   # cursor → frame start, clears to end of screen. No flush.
        _write(text)     # no flush
        _draw_input_frame(_output_buf_prefix[0], _output_buf_text[0])  # one flush

    def _outputln(text: str = ""):
        _erase_frame()
        _write(text + "\n")
        _draw_input_frame(_output_buf_prefix[0], _output_buf_text[0])

    _output_buf_prefix = [""]  # current mode prefix, kept for atomic redraws

    # ── Stderr interceptor: route any raw stderr through the frame-aware output ──
    class _StderrInterceptor:
        """Redirect stderr through _outputln so stray prints/warnings don't
        corrupt the input frame during LLM generation."""

        def __init__(self, real_stderr):
            self._real = real_stderr
            self._buf = ""

        def write(self, text: str):
            # Buffer until we have a complete line
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.rstrip("\r")
                if line:
                    _outputln(f"\033[2m{line}\033[0m")  # dimmed so it's visually distinct

        def flush(self):
            # Flush remaining buffer without a trailing newline
            if self._buf.strip():
                _outputln(f"\033[2m{self._buf.strip()}\033[0m")
                self._buf = ""

        def fileno(self):
            return self._real.fileno()

        def isatty(self):
            return self._real.isatty()

    _real_stderr = sys.stderr
    sys.stderr = _StderrInterceptor(_real_stderr)

    # Helper to full redraw (used by /clear and resize)
    def _redraw():
        nonlocal model_name, provider_name, cwd_display, session_name, cmd_count, _frame_drawn
        # Re-read model/provider in case it changed
        if client._is_full_brain and hasattr(brain, "reasoner"):
            try:
                providers = brain.reasoner.providers.get_active_providers()
                if providers:
                    model_name = providers[0].model or "local"
                    provider_name = providers[0].name or "local"
            except Exception:
                pass
        elif client._server_mode:
            try:
                import urllib.request as _ur, json as _jj
                resp = _ur.urlopen(f"{client._server_url}/api/providers", timeout=1)
                provs = _jj.loads(resp.read()).get("providers", [])
                if provs:
                    model_name = provs[0].get("model", model_name)
                    provider_name = provs[0].get("name", provider_name)
            except Exception:
                pass
        cwd_display = os.getcwd().replace(os.path.expanduser("~"), "~")
        if session_mgr.current:
            session_name = session_mgr.current.name or session_mgr.current.display_name
        _write("\033[2J\033[H")
        sys.stdout.flush()
        _frame_drawn = False
        _writeln(render_banner(model_name, provider_name, cwd_display, session_name, cmd_count))
        _writeln()

    # Handle terminal resize — deferred to event loop for async safety
    import signal
    _in_input = False  # Track if we're waiting for input
    _resize_pending = False

    def _handle_resize(signum, frame):
        nonlocal _resize_pending
        _resize_pending = True
        # Schedule immediate redraw via the event loop — don't wait for next keypress
        try:
            _loop = asyncio.get_event_loop()
            if _loop.is_running():
                _loop.call_soon_threadsafe(_process_resize)
        except Exception:
            pass

    def _process_resize():
        """Called from event loop to safely process pending resize."""
        nonlocal _resize_pending
        if not _resize_pending:
            return
        _resize_pending = False
        _redraw()   # clears screen and redraws banner
        if _in_input:
            _draw_input_frame(_get_mode_prefix())

    try:
        signal.signal(signal.SIGWINCH, _handle_resize)
    except (AttributeError, ValueError):
        pass

    class _ModelEntry:
        """Autocomplete entry for model names (duck-typed to match command objects)."""
        def __init__(self, name: str, description: str = ""):
            self.name = name
            self.description = description
            self.aliases = []
            self.is_model = True

    # ── Async input reader with slash command autocomplete ──
    async def _async_read_input(mode_prefix, tw):
        """Async input reader. Non-blocking so queries can stream concurrently.

        Returns the input string, or None on EOF.
        """
        import tty, termios

        # Load command list for autocomplete
        try:
            from src.commands import registry as _reg
            all_cmds = sorted(_reg.list_commands(include_hidden=False), key=lambda c: c.name)
        except Exception:
            all_cmds = []

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        buf = []
        menu_visible = False
        menu_lines = 0
        selected = 0
        loop = asyncio.get_event_loop()
        result_future = loop.create_future()

        # History browsing state — reload from session each time input is shown
        _history_entries = []
        try:
            if hasattr(session_mgr, 'current') and session_mgr.current:
                _history_entries = [m["content"] for m in session_mgr.current.messages if m["role"] == "user"]
        except Exception:
            pass
        # Also check a module-level history accumulator for this session
        if not hasattr(_async_read_input, '_session_history'):
            _async_read_input._session_history = []
        # Cap history to prevent unbounded memory growth
        _MAX_HISTORY = 1000
        if len(_async_read_input._session_history) > _MAX_HISTORY:
            _async_read_input._session_history = _async_read_input._session_history[-_MAX_HISTORY:]
        # Merge: session messages + any new ones typed this session
        for h in _async_read_input._session_history:
            if h not in _history_entries:
                _history_entries.append(h)
        _history_entries = _history_entries[-_MAX_HISTORY:]
        _history_idx = len(_history_entries)
        _saved_buf = []

        # Ctrl+R history search state
        _search_mode = False
        _search_buf = []
        _search_match_idx = 0

        # Escape sequence state
        _esc_buf = []
        _esc_timer = None

        def _redraw(hide_menu=True):
            """Redraw input line in the fixed zone."""
            text = "".join(buf)
            _output_buf_text[0] = text
            if hide_menu:
                _hide_menu()
            _draw_input_frame(mode_prefix, text)  # uses \033[2K per row — no erase needed

        MAX_VISIBLE = 6

        def _show_menu(matches):
            """Show autocomplete menu BELOW the footer (cursor at prompt)."""
            nonlocal menu_visible, menu_lines
            _hide_menu()
            if not matches:
                return
            total = len(matches)
            start = max(0, min(selected - MAX_VISIBLE // 2, total - MAX_VISIBLE))
            end = min(total, start + MAX_VISIBLE)

            visible = end - start
            extra = (1 if start > 0 else 0) + (1 if end < total else 0)
            total_menu_lines = visible + extra

            # Cursor is at prompt. Go down 3 to reach area below footer.
            _write("\033[3B\r")
            if start > 0:
                _write(f"\033[K    {DIM}↑ {start} more{RESET}\n")
            for i in range(start, end):
                cmd = matches[i]
                pfx = f"  {CYAN}❯{RESET} " if i == selected else "    "
                input_prefix = "".join(buf)[1:].lower()
                alias_hint = ""
                if input_prefix and not cmd.name.startswith(input_prefix):
                    for alias in (cmd.aliases or []):
                        if alias.lstrip("/").lower().startswith(input_prefix):
                            alias_hint = f" {DIM}(/{alias.lstrip('/')}){RESET}"
                            break
                desc = _truncate_display(cmd.description, max(20, tw - 40)) if cmd.description else ""
                _write(f"\033[K{pfx}{CYAN}/{cmd.name:<22s}{RESET}{alias_hint} {DIM}{desc}{RESET}\n")
            if end < total:
                _write(f"\033[K    {DIM}↓ {total - end} more{RESET}\n")
            menu_lines = total_menu_lines
            _write(f"\033[{menu_lines + 3}A")  # back to prompt
            menu_visible = True
            sys.stdout.flush()

        def _hide_menu():
            """Erase the autocomplete menu below the footer."""
            nonlocal menu_visible, menu_lines
            if not menu_visible:
                return
            _write("\033[3B\r")
            for i in range(menu_lines):
                _write("\033[K\n")
            _write(f"\033[{menu_lines + 3}A")  # back to prompt
            menu_visible = False
            menu_lines = 0
            sys.stdout.flush()

        # Cache of available models for autocomplete
        _model_entries_cache = []

        def _load_model_entries():
            nonlocal _model_entries_cache
            if _model_entries_cache:
                return _model_entries_cache
            entries = []
            try:
                from src.reasoning.providers import ProviderRegistry
                reg = ProviderRegistry()
                for p in reg.get_active_providers():
                    is_local = "localhost" in p.base_url or "127.0.0.1" in p.base_url
                    tag = "local" if is_local else "cloud"
                    for m in p.models:
                        entries.append(_ModelEntry(m, f"[{tag}] {p.name}"))
                try:
                    import urllib.request as _ur, json as _j
                    resp = _ur.urlopen("http://localhost:11434/api/tags", timeout=2)
                    ollama_models = [m["name"] for m in _j.loads(resp.read()).get("models", [])]
                    existing = {e.name for e in entries}
                    for m in ollama_models:
                        if m not in existing:
                            entries.append(_ModelEntry(m, "[local/ollama]"))
                except Exception:
                    pass
            except Exception:
                pass
            _model_entries_cache = entries
            return entries

        def _get_matches():
            nonlocal all_cmds
            text = "".join(buf)
            if not text.startswith("/"):
                return []

            # Sub-completion: /model <name>
            if text.lower().startswith("/model "):
                model_prefix = text[7:].lower()
                models = _load_model_entries()
                return [m for m in models if m.name.lower().startswith(model_prefix)]

            # Reload if commands weren't available at init (e.g., lazy registration)
            if not all_cmds:
                try:
                    from src.commands import registry as _reg2
                    all_cmds = sorted(_reg2.list_commands(include_hidden=False), key=lambda c: c.name)
                except Exception:
                    pass
            prefix = text[1:].lower()
            if not prefix:
                return list(all_cmds)  # Show all on bare "/"
            # Match command names AND aliases
            seen = set()
            matches = []
            for c in all_cmds:
                if c.name.startswith(prefix):
                    if c.name not in seen:
                        seen.add(c.name)
                        matches.append(c)
                    continue
                # Check aliases
                for alias in (c.aliases or []):
                    if alias.lstrip("/").lower().startswith(prefix):
                        if c.name not in seen:
                            seen.add(c.name)
                            matches.append(c)
                        break
            return matches

        def _show_shortcut_help():
            """Show instant shortcut overlay above the input frame (like Claude Code ?).

            Displays shortcuts in the output area, then redraws the input frame.
            """
            _erase_frame()
            sections = [
                ("Input", [
                    ("v", "Voice input"),
                    ("!cmd", "Run shell command"),
                    ("!!cmd", "Run + analyze output"),
                    ("/cmd", "Slash command"),
                ]),
                ("Navigation", [
                    ("Ctrl+C", "Cancel / clear input"),
                    ("Ctrl+D", "Exit (press twice)"),
                    ("Ctrl+L", "Clear screen"),
                    ("Ctrl+R", "Search history"),
                    ("Ctrl+E", "Open in $EDITOR"),
                    ("Ctrl+T", "Show recent queries"),
                    ("Up/Down", "Browse history"),
                    ("Tab", "Accept autocomplete"),
                    ("Esc", "Close menu / cancel"),
                ]),
                ("Quick Commands", [
                    ("/help", "All commands"),
                    ("/status", "Model, mode, session"),
                    ("/context", "Token usage"),
                    ("/doctor", "Health check"),
                    ("/model", "Switch AI model"),
                    ("/effort", "Set response depth"),
                    ("/compact", "Compress context"),
                    ("/new", "Fresh conversation"),
                    ("/cost", "Session cost summary"),
                ]),
            ]
            _write("\n")
            for section, items in sections:
                _write(f"  {BOLD}{section}{RESET}\n")
                for key, desc in items:
                    _write(f"    {CYAN}{key:<14s}{RESET} {DIM}{desc}{RESET}\n")
                _write("\n")
            sys.stdout.flush()

        def _draw_search_prompt():
            """Draw the Ctrl+R search prompt using absolute addressing — immune to cursor drift."""
            query = "".join(_search_buf)
            matches = _get_search_matches()
            match_text = ""
            if matches and _search_match_idx < len(matches):
                match_text = matches[_search_match_idx]
                max_len = _tw() - 4
                if _display_width(match_text) > max_len:
                    match_text = _truncate_display(match_text, max_len - 3) + "..."
                match_text = match_text.replace("\n", " ")

            R = _term_rows()
            sep = f"{DIM}{'─' * _tw()}{RESET}"

            _write("\033[?25l")  # hide cursor during redraw
            _write(f"\033[{R - 3};1H\033[2K{sep}")
            _write(f"\033[{R - 2};1H\033[2K{YELLOW}(reverse-i-search){RESET}: {query}{DIM} -> {match_text}{RESET}")
            _write(f"\033[{R - 1};1H\033[2K{sep}")
            _write(f"\033[{R};1H\033[2K  {DIM}Ctrl+R next | Enter accept | Esc cancel{RESET}")
            # Position cursor on search line after query text (columns are 1-indexed).
            cursor_col = len("(reverse-i-search): ") + _display_width(query) + 1
            _write(f"\033[{R - 2};{cursor_col}H")
            _write("\033[?25h")  # show cursor
            sys.stdout.flush()

        def _get_search_matches():
            """Get history entries matching the current search query."""
            query = "".join(_search_buf)
            if not query:
                return list(reversed(_history_entries))
            return [e for e in reversed(_history_entries) if query.lower() in e.lower()]

        def _process_char(ch):
            nonlocal selected, _esc_buf, _esc_timer
            nonlocal _search_mode, _search_buf, _search_match_idx
            nonlocal _history_idx, _saved_buf
            if result_future.done():
                return

            # ── Ctrl+R history search mode ──
            if _search_mode:
                if ch == "\x12":
                    # Ctrl+R again: cycle to next match
                    matches = _get_search_matches()
                    if matches:
                        _search_match_idx = (_search_match_idx + 1) % len(matches)
                    _draw_search_prompt()
                    return
                elif ch == "\n" or ch == "\r":
                    # Accept current match
                    matches = _get_search_matches()
                    if matches and _search_match_idx < len(matches):
                        buf.clear()
                        buf.extend(matches[_search_match_idx])
                    _search_mode = False
                    _search_buf.clear()
                    _search_match_idx = 0
                    _redraw()
                    return
                elif ch == "\x1b":
                    # Escape: cancel search
                    _search_mode = False
                    _search_buf.clear()
                    _search_match_idx = 0
                    _redraw()
                    return
                elif ch == "\x7f" or ch == "\x08":
                    # Backspace in search
                    if _search_buf:
                        _search_buf.pop()
                        _search_match_idx = 0
                    _draw_search_prompt()
                    return
                elif ch == "\x03":
                    # Ctrl+C: cancel search
                    _search_mode = False
                    _search_buf.clear()
                    _search_match_idx = 0
                    _redraw()
                    return
                elif ch >= " ":
                    _search_buf.append(ch)
                    _search_match_idx = 0
                    _draw_search_prompt()
                    return
                return

            # ── Single-key y/n shortcut when JARVIS asked a question ──
            if _last_was_question[0] and not buf and ch in ('y', 'Y', 'n', 'N'):
                answer = 'yes' if ch.lower() == 'y' else 'no'
                _last_was_question[0] = False
                buf.extend(answer)
                if not result_future.done():
                    result_future.set_result(answer)
                return
            # Clear yn hint the moment user starts typing anything else
            if _last_was_question[0] and ch >= ' ':
                _last_was_question[0] = False
                _redraw()

            # ── Normal input mode ──
            # Try keybinding resolver first (src/keybindings)
            _kb_action = resolve_keybinding("Chat", ch)
            if _kb_action == "app:interrupt":
                _hide_menu()
                if _active_task and not _active_task.done():
                    _active_task.cancel()
                    _outputln(f"\n  {DIM}Cancelled.{RESET}")
                buf.clear()
                _history_idx = len(_history_entries)
                _redraw()
                if not result_future.done():
                    result_future.set_result("")
                return
            elif _kb_action == "app:exit":
                _hide_menu()
                if not result_future.done():
                    result_future.set_result(None)
                return
            elif _kb_action == "app:redraw":
                _hide_menu()
                _redraw()   # teardown zones, clear, banner, setup_zones
                _draw_input_frame(mode_prefix, "".join(buf))
                return
            elif _kb_action == "history:search":
                _hide_menu()
                _search_mode = True
                _search_buf.clear()
                _search_match_idx = 0
                _draw_search_prompt()
                return
            # Fall through to legacy hardcoded handling (keybindings supplement, don't replace)

            if ch == "\n" or ch == "\r":
                if menu_visible:
                    matches = _get_matches()
                    if matches and selected < len(matches):
                        item = matches[selected]
                        buf.clear()
                        if getattr(item, 'is_model', False):
                            # Model entry selected from /model <filter> — run it directly
                            buf.extend(f"/model {item.name}")
                        else:
                            buf.extend(f"/{item.name}")
                _hide_menu()
                text = "".join(buf)
                # Multi-line: if text ends with \, continue on next line
                if text.endswith("\\"):
                    buf[-1] = "\n"  # replace trailing \ with newline
                    _redraw()
                    return
                text = text.strip()
                if text and (not _history_entries or _history_entries[-1] != text):
                    _history_entries.append(text)
                    if hasattr(_async_read_input, '_session_history'):
                        _async_read_input._session_history.append(text)
                _history_idx = len(_history_entries)
                if not result_future.done():
                    result_future.set_result(text)
            elif ch == "\x04":
                _hide_menu()
                if not result_future.done():
                    result_future.set_result(None)
            elif ch == "\x03":
                _hide_menu()
                # Ctrl+C: cancel active task if running, else clear input
                if _active_task and not _active_task.done():
                    _active_task.cancel()
                    _outputln(f"\n  {DIM}Cancelled.{RESET}")
                buf.clear()
                _history_idx = len(_history_entries)
                _redraw()
                if not result_future.done():
                    result_future.set_result("")
            elif ch == "\x0c":
                # Ctrl+L: Clear and redraw screen (full redraw, not just input)
                _hide_menu()
                _redraw()   # teardown zones, clear, banner, setup_zones
                _draw_input_frame(mode_prefix, "".join(buf))
            elif ch == "\x12":
                # Ctrl+R: Enter history search mode
                _hide_menu()
                _search_mode = True
                _search_buf.clear()
                _search_match_idx = 0
                _draw_search_prompt()
            elif ch == "\x05":
                # Ctrl+E: Open external editor
                _hide_menu()
                import tempfile
                editor = os.environ.get("EDITOR", "vi")
                tf_path = None
                with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
                    tf.write("".join(buf))
                    tf_path = tf.name
                try:
                    import termios as _termios
                    _termios.tcsetattr(fd, _termios.TCSADRAIN, old_settings)
                    _write("\033[r")  # Reset scroll region for editor
                    import shlex as _shlex
                    subprocess.run([*_shlex.split(editor), tf_path], check=False)
                    with open(tf_path, "r") as f:
                        new_text = f.read().strip()
                    buf.clear()
                    buf.extend(new_text)
                except (OSError, ValueError) as e:
                    log.debug("Editor launch failed: %s", e)
                finally:
                    # Always restore terminal to cbreak mode
                    try:
                        import tty as _tty
                        _tty.setcbreak(fd)
                    except Exception:
                        pass
                    if tf_path:
                        try:
                            os.unlink(tf_path)
                        except OSError:
                            pass
                _redraw()
            elif ch == "\x14":
                # Ctrl+T: Toggle task/todo summary
                _hide_menu()
                _outputln()
                _outputln(f"  {BOLD}Recent queries{RESET}")
                if _history_entries:
                    for i, entry in enumerate(_history_entries[-5:], 1):
                        preview = entry[:60].replace("\n", " ")
                        _outputln(f"    {DIM}{i}. {preview}{'...' if len(entry) > 60 else ''}{RESET}")
                else:
                    _outputln(f"    {DIM}No history in this session.{RESET}")
                _outputln()
                _draw_input_frame(mode_prefix, "".join(buf))
            elif ch == "\x7f" or ch == "\x08":
                if buf:
                    buf.pop()
                    will_show_menu = "".join(buf).startswith("/")
                    _redraw(hide_menu=not will_show_menu)
                    if will_show_menu:
                        matches = _get_matches()
                        selected = 0
                        _show_menu(matches)
                    else:
                        _hide_menu()
            elif ch == "\x09":
                if menu_visible:
                    matches = _get_matches()
                    if matches and selected < len(matches):
                        item = matches[selected]
                        buf.clear()
                        if getattr(item, 'is_model', False):
                            buf.extend(f"/model {item.name}")
                        else:
                            buf.extend(f"/{item.name} ")
                        _redraw()
                        _hide_menu()
            elif ch >= " ":
                # Vim NORMAL mode: intercept keys
                if _vim_enabled and isinstance(_vim_state, NormalState):
                    handled = _vim_handle_normal_key(ch, buf)
                    if handled:
                        _redraw()
                        return
                # ? on empty buffer: instant shortcut overlay (no Enter needed)
                if ch == "?" and not buf:
                    _hide_menu()
                    _show_shortcut_help()
                    _draw_input_frame(mode_prefix, "")
                    return
                # INSERT mode or vim disabled: normal input
                buf.append(ch)
                will_show_menu = "".join(buf).startswith("/")
                _redraw(hide_menu=not will_show_menu)
                if will_show_menu:
                    matches = _get_matches()
                    selected = 0
                    if matches:
                        _show_menu(matches)
                    else:
                        _hide_menu()
                else:
                    _hide_menu()

        _paste_mode = [False]
        _paste_buf: list[str] = []
        _paste_timeout = None

        def _paste_timeout_flush():
            """Safety: flush paste buffer if end marker never arrives."""
            nonlocal _paste_timeout
            _paste_timeout = None
            if _paste_mode[0]:
                _paste_mode[0] = False
                if _paste_buf:
                    pasted = "".join(_paste_buf).replace("\r\n", "\n").replace("\r", "\n")
                    buf.extend(pasted)
                    _paste_buf.clear()
                    _redraw()

        def _handle_escape_seq():
            """Process buffered escape sequence after timeout."""
            nonlocal selected, _esc_buf, _esc_timer, _paste_timeout
            nonlocal _history_idx, _saved_buf
            nonlocal _search_mode, _search_buf, _search_match_idx
            _esc_timer = None
            seq = "".join(_esc_buf)  # e.g. "[A" for up arrow
            _esc_buf.clear()

            # Bracketed paste — start marker
            if seq == "[200~":
                _paste_mode[0] = True
                _paste_buf.clear()
                # Safety timeout: if end marker never arrives, flush after 10s
                nonlocal _paste_timeout
                if _paste_timeout is not None:
                    _paste_timeout.cancel()
                _paste_timeout = loop.call_later(10.0, _paste_timeout_flush)
                return

            # Bracketed paste — end marker: flush paste buffer in one redraw
            if seq == "[201~":
                _paste_mode[0] = False
                if _paste_timeout is not None:
                    _paste_timeout.cancel()
                    _paste_timeout = None
                if _paste_buf:
                    pasted = "".join(_paste_buf).replace("\r\n", "\n").replace("\r", "\n")
                    buf.extend(pasted)
                    _paste_buf.clear()
                    _redraw()
                return

            # In search mode, Escape cancels
            if _search_mode and seq == "":
                _search_mode = False
                _search_buf.clear()
                _search_match_idx = 0
                _redraw()
                return

            if seq == "[A" and menu_visible:
                matches = _get_matches()
                if matches:
                    selected = min(selected, len(matches) - 1)
                    selected = max(0, selected - 1)
                _show_menu(matches)
            elif seq == "[B" and menu_visible:
                matches = _get_matches()
                if matches:
                    selected = min(len(matches) - 1, selected + 1)
                else:
                    selected = 0
                _show_menu(matches)
            elif seq == "[A" and not menu_visible:
                # Up arrow: previous history entry
                if _history_idx > 0:
                    if _history_idx == len(_history_entries):
                        _saved_buf = list(buf)
                    _history_idx -= 1
                    buf.clear()
                    buf.extend(_history_entries[_history_idx])
                    _redraw()
            elif seq == "[B" and not menu_visible:
                # Down arrow: next history entry
                if _history_idx < len(_history_entries):
                    _history_idx += 1
                    buf.clear()
                    if _history_idx == len(_history_entries):
                        buf.extend(_saved_buf)
                    else:
                        buf.extend(_history_entries[_history_idx])
                    _redraw()
            elif seq == "" and _vim_enabled:
                # Just Escape with vim enabled — switch to NORMAL mode
                if isinstance(_vim_state, InsertState):
                    _vim_state = enter_normal(_vim_state)
                _hide_menu()
            else:
                # Just Escape — close menu
                _hide_menu()

        def _on_stdin():
            nonlocal _esc_buf, _esc_timer
            if result_future.done():
                return
            # Process any pending resize before handling input
            _process_resize()
            try:
                data = os.read(fd, 32).decode("utf-8", errors="replace")
            except OSError:
                return

            for ch in data:
                if _paste_mode[0]:
                    # Inside bracketed paste — collect without redrawing
                    if ch == "\x1b":
                        # Could be the end marker \x1b[201~
                        _esc_buf.clear()
                        _esc_timer = loop.call_later(0.05, _handle_escape_seq)
                    elif _esc_timer is not None or len(_esc_buf) > 0:
                        _esc_buf.append(ch)
                        if len(_esc_buf) >= 2 and _esc_buf[0] == "[" and _esc_buf[-1] in "~ABCDHFPQRSMm":
                            if _esc_timer:
                                _esc_timer.cancel()
                                _esc_timer = None
                            _handle_escape_seq()
                    else:
                        _paste_buf.append(ch)
                elif _esc_timer is not None or len(_esc_buf) > 0:
                    # We're in an escape sequence (after \x1b)
                    _esc_buf.append(ch)
                    # Arrow keys: \x1b [ A/B/C/D — need exactly "[" + letter
                    # Also handle ~ terminated sequences (e.g. [200~ [201~ for bracketed paste)
                    if len(_esc_buf) >= 2 and _esc_buf[0] == "[" and _esc_buf[-1] in "~ABCDHFPQRSMm":
                        if _esc_timer:
                            _esc_timer.cancel()
                            _esc_timer = None
                        _handle_escape_seq()
                    elif len(_esc_buf) >= 2 and _esc_buf[0] != "[":
                        # Not a CSI sequence — flush
                        if _esc_timer:
                            _esc_timer.cancel()
                            _esc_timer = None
                        _handle_escape_seq()
                    elif len(_esc_buf) > 12:
                        # Too long — something went wrong, flush
                        if _esc_timer:
                            _esc_timer.cancel()
                            _esc_timer = None
                        _handle_escape_seq()
                elif ch == "\x1b":
                    # Cancel any pending timer before starting new sequence
                    if _esc_timer:
                        _esc_timer.cancel()
                        _esc_timer = None
                    if _esc_buf:
                        _handle_escape_seq()
                    _esc_buf.clear()
                    # Wait briefly for rest of sequence
                    _esc_timer = loop.call_later(0.05, _handle_escape_seq)
                else:
                    _process_char(ch)

        try:
            tty.setcbreak(fd)
            _write("\033[?2004h")  # enable bracketed paste mode
            sys.stdout.flush()
            loop.add_reader(fd, _on_stdin)
            result = await result_future
            return result
        except Exception:
            _hide_menu()
            return "".join(buf).strip()
        finally:
            try:
                loop.remove_reader(fd)
            except Exception:
                pass
            _write("\033[?2004l")  # disable bracketed paste mode
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    # ── Background Query Runner ──
    async def _run_query(user_input, voice_mode=False):
        """Run a query as a background task, outputting to the scroll region."""
        nonlocal _active_task

        from src.cli.display import (
            tool_call_line, tool_result_line, tool_result_preview,
            diff_display, token_footer as _token_footer,
            collapsed_tool_group, permission_prompt,
        )

        start = time.time()
        session_mgr.add_message("user", user_input)

        full_text = ""
        tool_count = 0
        _streaming_text = False
        _tool_states = []
        _tokens_this_turn = 0

        # Spinner state lives in main() scope (_spin_line_active, _spin_task_ref)
        # so _erase_frame() can cancel and clear the spinner atomically.
        _spin_label = ["Thinking..."]
        # Reset shared state at the start of each query
        _spin_line_active[0] = False
        _spin_task_ref[0] = None

        async def _spin_loop():
            i = 1
            t0 = time.time()
            try:
                while True:
                    await asyncio.sleep(0.12)
                    if not _spin_line_active[0]:
                        break
                    elapsed = time.time() - t0
                    frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
                    elapsed_str = f" {DIM}{elapsed:.0f}s{RESET}" if elapsed >= 2 else ""
                    # Save cursor (at prompt), jump 3 rows up to spinner line, update, restore.
                    _write(f"\0337\033[3A\r\033[K  {BLUE}{frame}{RESET} {DIM}{_spin_label[0]}{RESET}{elapsed_str}\0338")
                    sys.stdout.flush()
                    i += 1
            except asyncio.CancelledError:
                pass

        def _start_spin(label="Thinking..."):
            _stop_spin()
            _spin_label[0] = label
            # Erase frame, write spinner line + redraw frame — one atomic flush.
            _erase_frame()
            _write(f"  {BLUE}{SPINNER_FRAMES[0]}{RESET} {DIM}{label}{RESET}\033[K\n")
            _spin_line_active[0] = True
            _draw_input_frame(_output_buf_prefix[0], _output_buf_text[0])
            _spin_task_ref[0] = asyncio.get_event_loop().create_task(_spin_loop())

        def _stop_spin():
            t = _spin_task_ref[0]
            if t and not t.done():
                t.cancel()
            _spin_task_ref[0] = None
            if _spin_line_active[0]:
                # Clear spinner line: save cursor → go to spinner row → erase → restore.
                _write(f"\0337\033[3A\r\033[K\0338")
                _spin_line_active[0] = False
                sys.stdout.flush()

        _start_spin()

        try:
            async for event in client.query_stream(user_input):
                etype = event.get("type", "")

                if etype == "tool_call":
                    _stop_spin()
                    if _streaming_text:
                        _outputln()
                        _streaming_text = False
                    tool_count += 1
                    name = event.get("name", "")
                    args = event.get("args", {})
                    _tool_states.append({
                        "name": name, "args": args,
                        "start": time.time(), "lines": [], "error": False,
                    })
                    _outputln(tool_call_line(name, args))
                    _start_spin(f"{name}")

                elif etype == "tool_result":
                    _stop_spin()
                    result_text = event.get("content", event.get("result", ""))
                    name = event.get("name", "")
                    if _tool_states:
                        ts = _tool_states[-1]
                        elapsed_tool = time.time() - ts["start"]
                        is_error = (result_text.startswith("Error") or
                                    result_text.startswith("BLOCKED"))
                        ts["error"] = is_error
                        ts["lines"] = result_text.strip().split("\n") if result_text.strip() else []
                        _outputln(tool_result_line(name, result_text, not is_error, elapsed_tool))
                        if name == "edit_file" and ts["args"].get("old_string"):
                            diff = diff_display(
                                ts["args"]["old_string"],
                                ts["args"].get("new_string", ""),
                                ts["args"].get("path", ""),
                            )
                            if diff:
                                _outputln(diff)
                        if is_error:
                            _buddy_says("error")
                    _start_spin("Thinking...")

                elif etype == "dispatch":
                    _stop_spin()
                    if _streaming_text:
                        _outputln()
                        _streaming_text = False
                    tool_count += 1
                    agent_type = event.get("agent_type", "?")
                    task = event.get("task", "")[:60]
                    _tool_states.append({
                        "name": "dispatch", "args": {"agent_type": agent_type, "task": task},
                        "start": time.time(), "lines": [], "error": False,
                    })
                    _outputln(f"  {MAGENTA}◈{RESET} {DIM}agent:{agent_type}{RESET}  {task}")
                    _start_spin(f"agent:{agent_type}")

                elif etype == "dispatch_result":
                    _stop_spin()
                    if _tool_states and _tool_states[-1]["name"] == "dispatch":
                        elapsed_d = time.time() - _tool_states[-1]["start"]
                        _outputln(f"  {GREEN}✔{RESET} {DIM}agent done{RESET}  {DIM}{elapsed_d:.1f}s{RESET}")
                    _start_spin("Thinking...")

                elif etype == "text":
                    chunk = event.get("content", "")
                    if not chunk:
                        continue
                    if not _streaming_text:
                        _stop_spin()
                        _streaming_text = True
                        # Erase frame once, write prefix — cursor now inline in output.
                        _erase_frame()
                        _write(f"  {CYAN}●{RESET} ")
                    full_text += chunk
                    # Write chunk inline — no erase/redraw per token.
                    # Frame is redrawn once after streaming ends.
                    _write(chunk)
                    sys.stdout.flush()

                elif etype == "usage":
                    _tokens_this_turn += event.get("input_tokens", 0) + event.get("output_tokens", 0)

                elif etype == "error":
                    err = event.get("content", "Error")
                    if "rate_limit" in err or "413" in err or "too large" in err:
                        full_text = "Give me a moment — rate limited. Try again."
                    elif "No provider" in err:
                        full_text = "No AI provider available. Check /doctor."

                elif etype == "done":
                    pass

        except asyncio.CancelledError:
            # Keep any text already streamed; don't discard partial response
            _outputln(f"\n  {DIM}Cancelled.{RESET}")
        except Exception as e:
            full_text = f"Error: {str(e)[:80]}"
        finally:
            _stop_spin()

        if _streaming_text:
            _outputln()
        else:
            _output("\r\033[K")

        # Filter garbage
        if full_text:
            garbage_markers = ["<｜begin", "<|begin", "\\boxed{", "\\frac{", "\\sqrt{",
                               "begin▁of▁sentence", "Question: How do you solve"]
            if any(m in full_text for m in garbage_markers):
                full_text = "Sorry, I got confused there. Could you rephrase that?"

        if full_text.strip() and not _streaming_text:
            _outputln(f"  {CYAN}●{RESET} {render_markdown(full_text.strip())}")

        # ── Turn summary footer ────────────────────────────────────────
        elapsed_turn = time.time() - start
        _summary_parts = [f"{elapsed_turn:.1f}s"]
        if tool_count:
            _summary_parts.append(f"{tool_count} tool{'s' if tool_count != 1 else ''}")
        try:
            from src.agent.cost_tracker import get_tracker as _get_ct
            _cost = _get_ct().get_session_cost()
            if _cost > 0.0001:
                _summary_parts.append(f"${_cost:.4f}")
        except Exception:
            pass
        _outputln(f"  {DIM}─  {' · '.join(_summary_parts)}{RESET}")

        # Clean finish
        if full_text.strip() and tool_count > 0:
            _buddy_says("success")

        if full_text.strip():
            session_mgr.add_message("jarvis", full_text)
            # Detect yes/no question — enables single-key y/n shortcut in prompt.
            # Split on sentence boundaries; check if any sentence ends with '?'.
            # Strips markdown formatting (**, ``) before checking to avoid false negatives.
            import re as _re_yn
            _yn_sentences = _re_yn.split(r'(?<=[.!?])\s+', full_text.strip())
            _last_was_question[0] = any(
                s.rstrip('*_` \t').endswith('?') for s in _yn_sentences
            )

            # TTS if voice mode
            if voice_mode and full_text.strip():
                try:
                    spoken = full_text.strip()
                    import re as _re
                    spoken = _re.sub(r'```[\s\S]*?```', '', spoken)
                    spoken = _re.sub(r'`[^`]+`', '', spoken)
                    spoken = _re.sub(r'[#*_~>\-]', '', spoken)
                    spoken = spoken.strip()
                    if len(spoken) > 500:
                        spoken = spoken[:500] + "..."
                    if spoken and len(spoken) > 3:
                        if client._server_mode:
                            import urllib.request
                            tts_url = f"{client._server_url}/api/tts?text={urllib.request.quote(spoken[:300])}"
                            import subprocess as _sp
                            _sp.Popen(
                                ["mpv", "--no-video", "--really-quiet", tts_url],
                                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                                start_new_session=True,
                            )
                        else:
                            import edge_tts, tempfile
                            async def _speak():
                                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                                    tmp = f.name
                                communicate = edge_tts.Communicate(spoken[:300], "en-US-AndrewMultilingualNeural")
                                await communicate.save(tmp)
                                import subprocess as _sp2
                                _sp2.Popen(
                                    ["mpv", "--no-video", "--really-quiet", tmp],
                                    stdout=_sp2.DEVNULL, stderr=_sp2.DEVNULL,
                                    start_new_session=True,
                                )
                            asyncio.get_event_loop().create_task(_speak())
                except Exception:
                    pass

        # Redraw input frame after query completes
        _outputln()
        _draw_input_frame(_get_mode_prefix())
        _active_task = None

    def _get_mode_prefix():
        if client._is_full_brain and brain.mode != "normal":
            return f"{YELLOW}{brain.mode}{RESET} "
        return ""

    # ── Main REPL Loop ──
    _setup_zones()

    while True:
        try:
            if initial_query:
                user_input = initial_query
                initial_query = ""
            else:
                mode_prefix = _get_mode_prefix()
                _output_buf_prefix[0] = mode_prefix   # keep in sync for atomic redraws
                tw = _tw()

                try:
                    _draw_input_frame(mode_prefix)

                    _in_input = True
                    user_input = await _async_read_input(mode_prefix, tw)
                    _in_input = False
                    if user_input is None:
                        raise EOFError

                    if not user_input:
                        continue
                except EOFError:
                    _outputln()
                    _outputln(f"  {DIM}Press Ctrl+D again to exit, or keep typing.{RESET}")
                    _draw_input_frame(mode_prefix)
                    try:
                        user_input = await _async_read_input(mode_prefix, tw)
                        if user_input is None:
                            break  # Second Ctrl+D
                        if not user_input:
                            continue
                    except EOFError:
                        break

            if not user_input:
                continue  # stay in place — frame already drawn, loop will redraw in-place
            _cancelled = False
            _voice_mode = False

            # Echo user input — highlighted bar with ❯ like Claude Code
            tw = _tw()
            visible_len = len(user_input) + 4  # "  ❯ " prefix
            pad = max(0, tw - visible_len)
            _outputln(f"\033[48;5;236m  {YELLOW}❯{RESET}\033[48;5;236m \033[1;97m{user_input}{' ' * pad}\033[0m")

            # ═══ VOICE INPUT ═══
            if user_input in ("v", "/voice", "/speak", "/mic", "/listen"):
                try:
                    import sounddevice as sd
                    import numpy as np
                    _outputln(f"  {CYAN}🎤 Listening...{RESET} (speak now, 5 seconds)")
                    audio = sd.rec(int(5 * 16000), samplerate=16000, channels=1, dtype='float32')
                    sd.wait()
                    audio = audio.flatten()
                    rms = float(np.sqrt(np.mean(audio ** 2)))
                    if rms < 0.001:
                        _outputln(f"  {DIM}No speech detected.{RESET}")
                        continue

                    _outputln(f"  {DIM}Transcribing...{RESET}")
                    if client._server_mode:
                        try:
                            import aiohttp
                            audio_bytes = (audio * 32767).astype(np.int16).tobytes()
                            async with aiohttp.ClientSession() as sess:
                                form = aiohttp.FormData()
                                form.add_field('audio', audio_bytes,
                                               filename='audio.raw',
                                               content_type='application/octet-stream')
                                async with sess.post(
                                    f"{client._server_url}/api/transcribe",
                                    data=form, timeout=aiohttp.ClientTimeout(total=10)
                                ) as resp:
                                    data = await resp.json()
                                    text = data.get("text", "")
                        except Exception:
                            from src.speech.stt import transcribe_audio
                            text = transcribe_audio(audio, 16000)
                    else:
                        from src.speech.stt import transcribe_audio
                        text = transcribe_audio(audio, 16000)

                    if text:
                        _outputln(f"  {GREEN}You said:{RESET} {text}")
                        user_input = text
                        _voice_mode = True
                    else:
                        _outputln(f"  {DIM}Couldn't make that out. Try again.{RESET}")
                        continue
                except ImportError:
                    _outputln(f"  {RED}Voice needs: pip install sounddevice numpy{RESET}")
                    continue
                except Exception as e:
                    _outputln(f"  {RED}Voice error: {e}{RESET}")
                    continue

            # ═══ ? SHORTCUT HELP (JARVIS style) ═══
            if user_input == "?":
                _outputln()
                sections = [
                    ("Input", [
                        ("v", "Voice input"),
                        ("!cmd", "Run shell command"),
                        ("!!cmd", "Run + analyze output"),
                        ("/cmd", "Slash command"),
                    ]),
                    ("Navigation", [
                        ("Ctrl+C", "Cancel current operation"),
                        ("Ctrl+D", "Exit (press twice)"),
                        ("Ctrl+L", "Clear screen"),
                        ("Ctrl+R", "Search history"),
                        ("Ctrl+E", "Open in $EDITOR"),
                        ("Ctrl+T", "Show recent queries"),
                        ("Up/Down", "Browse history"),
                        ("Tab", "Accept autocomplete"),
                        ("Esc", "Close autocomplete menu"),
                    ]),
                    ("Quick Commands", [
                        ("/help", "All 123 commands"),
                        ("/status", "Model, mode, session"),
                        ("/context", "Token usage"),
                        ("/doctor", "Health check"),
                        ("/model", "Switch AI model"),
                        ("/effort", "Set response depth"),
                        ("/compact", "Compress context"),
                        ("/new", "Fresh conversation"),
                        ("/rewind", "Undo last exchange"),
                        ("/cost", "Session cost summary"),
                        ("/export", "Save conversation"),
                    ]),
                ]
                for section, items in sections:
                    _outputln(f"  {BOLD}{section}{RESET}")
                    for key, desc in items:
                        _outputln(f"    {CYAN}{key:<14s}{RESET} {DIM}{desc}{RESET}")
                    _outputln()
                continue

            # ═══ SLASH COMMANDS ═══
            if user_input.startswith("/"):
                parts = user_input[1:].split(None, 1)
                cmd_name = parts[0] if parts else ""
                cmd_args = parts[1] if len(parts) > 1 else ""

                # Just "/" alone — show command menu with descriptions (JARVIS style)
                if not cmd_name:
                    try:
                        from src.commands import registry as _reg
                        tw = _tw()
                        cmds = _reg.list_commands(include_hidden=False)
                        cmds.sort(key=lambda c: c.name)
                        _outputln()
                        for cmd in cmds:
                            name_col = f"  {CYAN}/{cmd.name}{RESET}"
                            pad = max(1, 42 - len(cmd.name) - 3)
                            desc = cmd.description[:tw - 46] if cmd.description else ""
                            _outputln(f"{name_col}{' ' * pad}{DIM}{desc}{RESET}")
                        _outputln()
                        _outputln(f"  {DIM}{len(cmds)} commands available. Type /command to run.{RESET}")
                        _outputln()
                    except Exception as e:
                        _outputln(f"  {DIM}Type /help for all commands. ({e}){RESET}")
                    continue

                # ─── fzf-based pickers for commands with options/args ─────────
                if not cmd_args and cmd_name in _COMMAND_OPTIONS:
                    opts = _COMMAND_OPTIONS[cmd_name]
                    lines = "\n".join(f"{o[0]}\t{o[1]}" for o in opts)
                    _erase_frame()
                    try:
                        chosen_line = await _fzf(
                            ["fzf", "--prompt", f"/{cmd_name} > ", "--height=40%",
                             "--layout=reverse", "--border=rounded",
                             "--with-nth=1", "--delimiter=\t",
                             "--preview-window=hidden", "--no-multi",
                             "--header=↑/↓ navigate  Enter select  Esc cancel"],
                            lines,
                        )
                    finally:
                        _draw_input_frame(_get_mode_prefix())
                    if chosen_line:
                        val = chosen_line.split("\t")[0].strip()
                        user_input = f"/{cmd_name} {val}"
                        cmd_args = val
                    else:
                        continue

                elif not cmd_args and cmd_name in _COMMAND_PROMPTS:
                    info = _COMMAND_PROMPTS[cmd_name]
                    _erase_frame()
                    try:
                        out = await _fzf(
                            ["fzf", "--prompt", f"{info.get('title', cmd_name)}: ",
                             "--height=40%", "--layout=reverse", "--border=rounded",
                             "--print-query", "--no-multi", "--no-info",
                             "--header", info.get("desc", ""),
                             "--phony"],
                        )
                        val = out.splitlines()[0] if out else ""
                    finally:
                        _draw_input_frame(_get_mode_prefix())
                    if val:
                        user_input = f"/{cmd_name} {val}"
                        cmd_args = val
                    else:
                        continue

                elif not cmd_args and cmd_name in _COMMAND_FLOWS:
                    steps = _COMMAND_FLOWS[cmd_name]
                    collected = []
                    cancelled = False
                    _erase_frame()
                    try:
                        for step in steps:
                            is_optional = step.get("optional", False)
                            if step["type"] == "pick":
                                lines2 = "\n".join(f"{o[0]}\t{o[1]}" for o in step["options"])
                                hint = "↑/↓ navigate  Enter select  Esc skip" if is_optional else "↑/↓ navigate  Enter select  Esc cancel"
                                chosen2 = await _fzf(
                                    ["fzf", "--prompt", f"{step['title']} > ",
                                     "--height=40%", "--layout=reverse", "--border=rounded",
                                     "--with-nth=1", "--delimiter=\t",
                                     f"--header={hint}"],
                                    lines2,
                                )
                                if not chosen2:
                                    if is_optional:
                                        break  # skip optional step, keep collected so far
                                    cancelled = True; break
                                collected.append(chosen2.split("\t")[0].strip())
                            elif step["type"] == "input":
                                val2 = await _fzf(
                                    ["fzf", "--prompt", f"{step['title']}: ",
                                     "--height=40%", "--layout=reverse", "--border=rounded",
                                     "--print-query", "--no-multi", "--no-info",
                                     "--phony"],
                                )
                                val2 = val2.splitlines()[0] if val2 else ""
                                if not val2:
                                    if is_optional:
                                        break
                                    cancelled = True; break
                                collected.append(val2)
                    finally:
                        _draw_input_frame(_get_mode_prefix())
                    if cancelled or not collected:
                        continue
                    user_input = f"/{cmd_name} {' '.join(collected)}"
                    cmd_args = " ".join(collected)

                # ─── model picker via fzf ──────────────────────────────────────
                elif cmd_name == "model" and not cmd_args:
                    entries = await _fetch_model_entries(client)
                    if entries:
                        lines_m = "\n".join(f"{e[2]}\t{e[0]}" for e in entries)
                        _erase_frame()
                        try:
                            chosen_m = await _fzf(
                                ["fzf", "--prompt", "model > ", "--height=40%",
                                 "--layout=reverse", "--border=rounded",
                                 "--with-nth=2", "--delimiter=\t",
                                 "--header=↑/↓ navigate  Enter select  Esc cancel"],
                                lines_m,
                            )
                        finally:
                            _draw_input_frame(_get_mode_prefix())
                        if chosen_m:
                            mname = chosen_m.split("\t")[0].strip()
                            _outputln()
                            if client._server_mode:
                                async for ev in client.query_stream(f"/model {mname}"):
                                    if ev.get("type") == "text" and ev.get("content"):
                                        _outputln(render_markdown(ev["content"]))
                                # Update local display to reflect the switch
                                model_name = mname
                            else:
                                from src.commands.registry import CommandContext as _CC
                                from src.commands import registry as _creg
                                _ctx = _CC(brain=brain, session_mgr=session_mgr,
                                           raw_input=f"/model {mname}", args=mname, mode=brain.mode)
                                _r = await _creg.dispatch("model", _ctx)
                                _outputln(_r.text if _r else f"Switched to {mname}")
                                # Refresh model_name from providers
                                if client._is_full_brain and hasattr(brain, "reasoner"):
                                    try:
                                        _provs = brain.reasoner.providers.get_active_providers()
                                        if _provs:
                                            model_name = _provs[0].model or mname
                                            provider_name = _provs[0].name or provider_name
                                    except Exception:
                                        model_name = mname
                            _outputln()
                            _redraw()  # refresh banner + footer with new model name
                    continue

                # ─── Generic fzf prompt for any command that needs arguments ──
                elif not cmd_args:
                    try:
                        from src.commands import registry as _areg
                        _cmd_obj = next((c for c in _areg.list_commands(include_hidden=True) if c.name == cmd_name), None)
                        _usage = getattr(_cmd_obj, 'usage', '') or ''
                        _desc  = getattr(_cmd_obj, 'description', '') or ''
                        if _cmd_obj and '<' in _usage:
                            _erase_frame()
                            _fzf_ran = False
                            _val_g = ""
                            try:
                                _out_g = await _fzf(
                                    ["fzf", "--prompt", f"/{cmd_name}: ",
                                     "--height=40%", "--layout=reverse", "--border=rounded",
                                     "--print-query", "--phony", "--no-info",
                                     "--header", f"{_desc}  |  usage: {_usage}"],
                                )
                                _val_g = (_out_g.splitlines() or [""])[0].strip()
                                _fzf_ran = True
                            except Exception:
                                pass  # fzf not installed — fall through to dispatch
                            finally:
                                _draw_input_frame(_get_mode_prefix())
                            if _val_g:
                                user_input = f"/{cmd_name} {_val_g}"
                                cmd_args = _val_g
                            # else: fzf returned empty or not available → fall through
                            # dispatch with empty args so handler shows its usage text
                    except Exception:
                        pass

                # CLI-only shortcuts
                if cmd_name == "visual" and cmd_args:
                    import shlex as _shlex_v
                    subprocess.Popen(
                        ["x-terminal-emulator", "-e", "bash", "-c",
                         f"{_shlex_v.quote(cmd_args)}; echo; echo [DONE]; read"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                    )
                    continue

                # Dispatch command locally (non-interactive commands)
                result = None
                try:
                    from src.commands import registry as cmd_registry
                    from src.commands.registry import CommandContext
                    ctx = CommandContext(
                        brain=brain,  # always set: real Brain or _BrainProxy routing to server
                        session_mgr=session_mgr, raw_input=user_input,
                        args=cmd_args, mode=brain.mode,
                    )
                    result = await cmd_registry.dispatch(cmd_name, ctx)
                except Exception as e:
                    logging.getLogger("jarvis.cli").debug("Command /%s dispatch error: %s", cmd_name, e)
                    _outputln(f"  {DIM}Command error: {e}{RESET}")

                if result is not None:
                    if result.action == "exit":
                        _teardown_zones()
                        session_mgr.save_current()
                        await client.close()
                        session_mgr.close()
                        _exit_alt_screen()
                        print("Session saved. JARVIS offline.")
                        return
                    elif result.action == "clear":
                        _redraw()
                    elif result.text:
                        _outputln()
                        _outputln(result.text)
                        _outputln()

                    # ── Post-dispatch display refresh ─────────────────────────
                    # Update banner or footer for commands that change visible state.
                    # Rules:
                    #   - banner_change=True  → full _redraw() (clears screen, redraws banner + frame)
                    #   - frame_only=True     → just redraw the input frame footer (no screen clear)
                    if getattr(result, 'success', True):
                        banner_change = False
                        frame_only = False

                        # Model / provider name changes → update banner
                        if cmd_name in ("model", "m"):
                            if client._is_full_brain and hasattr(brain, "reasoner"):
                                try:
                                    _provs = brain.reasoner.providers.get_active_providers()
                                    if _provs:
                                        model_name = _provs[0].model or model_name
                                        provider_name = _provs[0].name or provider_name
                                except Exception:
                                    pass
                            banner_change = True

                        # Theme / color — update live ANSI codes, then banner for color change
                        elif cmd_name in ("theme",):
                            _tval = (cmd_args or "").strip().split()[0] if cmd_args else ""
                            if _tval in ("dark", "light", "auto"):
                                _apply_theme(_tval)
                            else:
                                _apply_theme(_load_theme())
                            banner_change = True

                        elif cmd_name in ("color",):
                            banner_change = True

                        # Vim mode toggle — update local flag, refresh frame indicator
                        elif cmd_name in ("vim",):
                            _tval = (cmd_args or "").strip().lower()
                            if _tval == "on":
                                _vim_enabled = True
                            elif _tval == "off":
                                _vim_enabled = False
                            elif _tval == "toggle":
                                _vim_enabled = not _vim_enabled
                            frame_only = True

                        # Session name changes → update banner
                        elif cmd_name in ("rename", "session", "new", "resume",
                                         "stash", "pop", "load"):
                            try:
                                if session_mgr.current:
                                    session_name = (
                                        session_mgr.current.name
                                        or getattr(session_mgr.current, 'display_name', '')
                                        or "session"
                                    )
                            except Exception:
                                pass
                            banner_change = True

                        # Mode, permissions, effort — footer/prompt reads these live
                        elif cmd_name in ("mode", "permissions", "perms", "effort"):
                            frame_only = True

                        if banner_change:
                            _redraw()
                        elif frame_only:
                            _erase_frame()
                            _draw_input_frame(_get_mode_prefix())

                    continue
                elif not client._server_mode:
                    # Unknown command — try fuzzy suggestion
                    try:
                        from src.commands import registry as cmd_registry
                        suggestions = cmd_registry.suggest(cmd_name, limit=3)
                        if suggestions:
                            names = ", ".join(f"/{s.name}" for s in suggestions)
                            _outputln(f"  {DIM}Unknown command: /{cmd_name}. Did you mean: {names}?{RESET}")
                        else:
                            _outputln(f"  {DIM}Unknown command: /{cmd_name}. Type /help for commands.{RESET}")
                    except Exception:
                        _outputln(f"  {DIM}Unknown command: /{cmd_name}{RESET}")
                    continue

            # ═══ LOCAL SHORTCUT: launch desktop overlay ═══
            _ui_clean = re.sub(r'[^\w\s]', '', user_input.lower()).strip()
            _desktop_triggers = (
                "switch to desktop", "go to desktop", "move to desktop",
                "desktop mode", "jarvis desktop", "back to desktop",
                "open desktop", "launch desktop", "start desktop",
            )
            if any(t in _ui_clean for t in _desktop_triggers):
                _disp = os.environ.get("DISPLAY", "")
                if not _disp:
                    try:
                        with open("/tmp/.jarvis-display") as f:
                            _disp = f.read().strip()
                    except OSError:
                        _disp = ":0"
                _jarvis_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                env = {**os.environ, "DISPLAY": _disp,
                       "JARVIS_NO_SANDBOX": "1", "JARVIS_OWNER": "ulrich"}
                # Kill any existing desktop instance via PID file
                _pid_file = "/tmp/.jarvis-desktop.pid"
                try:
                    if os.path.exists(_pid_file):
                        with open(_pid_file) as f:
                            _old_pid = int(f.read().strip())
                        os.kill(_old_pid, 15)  # SIGTERM
                        time.sleep(0.5)
                except Exception:
                    pass
                subprocess.Popen(
                    [os.path.join(_jarvis_root, "src", "desktop-tauri", "src-tauri", "target", "release", "jarvis-desktop")],
                    cwd=_jarvis_root, start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                )
                _outputln(f"  {CYAN}●{RESET} Desktop overlay launching on this machine.")
                continue

            # ═══ SHELL SHORTCUT: !command ═══
            if user_input.startswith("!"):
                cmd = user_input[1:].strip()
                if not cmd:
                    continue
                analyze = cmd.startswith("!")
                if analyze:
                    cmd = cmd[1:].strip()

                _outputln(f"  {DIM}$ {cmd}{RESET}")
                try:
                    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
                    output = proc.stdout or proc.stderr or "(no output)"
                except subprocess.TimeoutExpired:
                    output = "Timed out."
                except Exception as e:
                    output = str(e)

                if output.strip():
                    rendered = format_tool_result("bash", output)
                    if rendered:
                        _outputln(render_markdown(rendered))

                session_mgr.add_message("user", f"!{cmd}")
                session_mgr.add_message("jarvis", output[:500])

                if analyze and output.strip():
                    _outputln(f"  {DIM}Analyzing...{RESET}")
                    analysis = await client.query(f"Analyze this output:\n{output[:2000]}")
                    _outputln(render_markdown(analysis))
                    session_mgr.add_message("jarvis", analysis)
                continue

            # ═══ MAIN QUERY — launch as background task ═══
            _output_buf_text[0] = ""  # clear input text before thinking starts
            _active_task = asyncio.get_event_loop().create_task(
                _run_query(user_input, voice_mode=_voice_mode)
            )

        except KeyboardInterrupt:
            _outputln()
            _cancelled = True
            # Cancel active query if running
            if _active_task and not _active_task.done():
                _active_task.cancel()
                _outputln(f"  {DIM}Cancelled.{RESET}")
            try:
                _outputln(f"  {DIM}Ctrl+C again to quit, or keep typing.{RESET}")
                await asyncio.sleep(1.5)
            except (KeyboardInterrupt, asyncio.CancelledError):
                _teardown_zones()
                session_mgr.save_current()
                await client.close()
                session_mgr.close()
                _exit_alt_screen()
                print("Session saved. JARVIS offline.")
                return
        except EOFError:
            _teardown_zones()
            session_mgr.save_current()
            await client.close()
            session_mgr.close()
            _exit_alt_screen()
            print("Session saved. JARVIS offline.")
            return
        except Exception as e:
            msg = str(e)
            # Suppress harmless asyncio transport noise (aiohttp keepalive races)
            if "closing transport" in msg or "connection lost" in msg.lower():
                pass
            else:
                _outputln(f"  {RED}✘ {e}{RESET}\n")


def run():
    try:
        _app()
    except (KeyboardInterrupt, SystemExit, RuntimeError):
        pass  # Handled in finally
    finally:
        # Ensure alt screen is exited on abrupt kill (real terminals only)
        try:
            if sys.stdout.isatty() and not (
                os.environ.get("TERM_PROGRAM") == "vscode"
                or "VSCODE_INJECTION" in os.environ
                or "VSCODE_GIT_IPC_HANDLE" in os.environ
            ):
                sys.stdout.write("\033[?1049l")
                sys.stdout.flush()
        except Exception:
            pass
        # Restore real stderr (undo our interceptor) before final devnull redirect
        try:
            if hasattr(sys.stderr, "_real"):
                sys.stderr = sys.stderr._real
        except Exception:
            pass
        # Suppress aiohttp cleanup errors that print after event loop closes
        # These are harmless but ugly — redirect stderr to devnull during shutdown
        try:
            import os
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 2)  # Redirect stderr
            os.close(devnull)
        except Exception:
            pass


if __name__ == "__main__":
    run()
