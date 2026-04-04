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
import argparse
import threading
import logging
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Suppress noisy library logs from polluting the terminal
logging.getLogger("numexpr").setLevel(logging.ERROR)
logging.getLogger("numexpr.utils").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module="numexpr")

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

    def tick(self, label: str):
        frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        self._frame += 1
        _clear_line()
        _write(f"  {BLUE}{frame}{RESET} {label}")
        self._active = True

    def done(self, label: str):
        _clear_line()
        _writeln(f"  {GREEN}✔{RESET} {label}")
        self._active = False

    def fail(self, label: str):
        _clear_line()
        _writeln(f"  {RED}✘{RESET} {label}")
        self._active = False

    def clear(self):
        if self._active:
            _clear_line()
            self._active = False


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
    # Truncate long results
    lines = result.strip().split("\n")
    if len(lines) > 30:
        display = "\n".join(lines[:25]) + f"\n... ({len(lines) - 25} more lines)"
    else:
        display = result.strip()
    if len(display) > 3000:
        display = display[:3000] + "\n... (truncated)"
    return f"### Tool `{name}`\n\n```text\n{display}\n```"


# ── Standalone Brain ─────────────────────────────────────────────────

class StandaloneBrain:
    """Connects to JARVIS server (shared Brain) or falls back to local Brain."""

    def __init__(self):
        self.brain = None
        self._is_full_brain = True
        self._server_mode = False
        self._server_url = "http://localhost:8765"
        self._ws_url = "ws://localhost:8765/ws"
        self._ws = None

    async def connect(self) -> bool:
        # Try connecting to running JARVIS server first (shared Brain)
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._server_url}/api/mesh/ping", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        self._server_mode = True
                        self._is_full_brain = False
                        # Connect WebSocket for streaming
                        self._session = aiohttp.ClientSession()
                        self._ws = await self._session.ws_connect(self._ws_url)
                        return True
        except Exception:
            pass

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
        return await self.brain.think(text)

    async def query_stream(self, text: str):
        if self._server_mode and self._ws:
            import json
            # Send query via WebSocket
            await self._ws.send_json({"type": "query", "text": text})
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


# ── CLI Entry ────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="JARVIS — autonomous AI agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  jarvis                        Start interactive session\n"
               "  jarvis -c                     Continue last session\n"
               "  jarvis -r my-project          Resume named session\n"
               "  jarvis -p 'list files'        One-shot print mode\n"
               "  cat log.txt | jarvis -p 'analyze this'\n",
    )
    parser.add_argument("-c", "--continue", dest="continue_last", action="store_true",
                        help="Continue the most recent session")
    parser.add_argument("-r", "--resume", type=str, metavar="NAME",
                        help="Resume a session by name or ID")
    parser.add_argument("-p", "--print", dest="print_mode", type=str, metavar="QUERY",
                        help="One-shot mode: run query and print result")
    parser.add_argument("-m", "--mode", type=str, default="normal",
                        choices=["normal", "agent", "cli", "berbon", "plan"],
                        help="Starting mode (default: normal)")
    parser.add_argument("-n", "--name", type=str, default="",
                        help="Name for the new session")
    parser.add_argument("--serve", action="store_true",
                        help="Start as MCP server (stdio mode)")
    parser.add_argument("--theme", type=str, choices=["dark", "light", "auto"],
                        help="Color theme (dark/light/auto)")
    parser.add_argument("query", nargs="*", help="Initial query")

    # ── New flags (ported from JARVIS) ──

    # Model & effort
    parser.add_argument("--model", type=str, metavar="MODEL",
                        help="Override model (aliases: opus, sonnet, haiku, or full name)")
    parser.add_argument("--effort", type=str, choices=["low", "medium", "high", "max"],
                        help="Response effort level")
    parser.add_argument("--fallback-model", type=str, metavar="MODEL",
                        help="Fallback model on overload")

    # Output formatting
    parser.add_argument("--output-format", type=str, default="text",
                        choices=["text", "json", "stream-json"],
                        help="Output format for print mode")
    parser.add_argument("--json-schema", type=str, metavar="SCHEMA",
                        help="JSON schema for structured output validation")

    # Limits
    parser.add_argument("--max-turns", type=int, metavar="N",
                        help="Max agentic turns in non-interactive mode")
    parser.add_argument("--max-budget-usd", type=float, metavar="USD",
                        help="Max spend for this session")

    # System prompt
    parser.add_argument("--system-prompt", type=str, metavar="PROMPT",
                        help="Custom system prompt")
    parser.add_argument("--system-prompt-file", type=str, metavar="FILE",
                        help="Read system prompt from file")
    parser.add_argument("--append-system-prompt", type=str, metavar="PROMPT",
                        help="Append to default system prompt")

    # Advanced
    parser.add_argument("--bare", action="store_true",
                        help="Minimal mode: skip hooks, plugins, MCP discovery")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output (show full tool results)")
    parser.add_argument("--debug", nargs="?", const="all", metavar="FILTER",
                        help="Debug mode (filter: api,hooks,tools)")
    parser.add_argument("--thinking", type=str, choices=["enabled", "adaptive", "disabled"],
                        help="Thinking mode")

    # Permission
    parser.add_argument("--permission-mode", type=str,
                        choices=["default", "bypass", "accept-edits", "plan"],
                        help="Permission prompting mode")
    parser.add_argument("--dangerously-skip-permissions", action="store_true",
                        help="Skip all permission checks")

    # Tools
    parser.add_argument("--tools", nargs="*", metavar="TOOL",
                        help="Specify available tools")
    parser.add_argument("--allowed-tools", nargs="*", metavar="TOOL",
                        help="Tool allowlist")
    parser.add_argument("--disallowed-tools", nargs="*", metavar="TOOL",
                        help="Tool denylist")

    # MCP
    parser.add_argument("--mcp-config", type=str, metavar="FILE",
                        help="MCP server config file")

    # Worktree
    parser.add_argument("-w", "--worktree", nargs="?", const="auto", metavar="NAME",
                        help="Create git worktree for this session")

    return parser.parse_args()


async def main():
    args = parse_args()

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
    logging.getLogger("brain").setLevel(logging.WARNING)
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

    # In server mode, brain is None — create a lightweight proxy for .mode etc.
    class _BrainProxy:
        mode = "normal"
        _pending_fixes = []
        _companion = None
        def dispatch_command(self, *a, **kw): return None
    if brain is None:
        brain = _BrainProxy()

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
        logging.getLogger("brain").setLevel(logging.DEBUG)
        if args.debug != "all":
            for filt in args.debug.split(","):
                logging.getLogger(f"brain.{filt.strip()}").setLevel(logging.DEBUG)

    # System prompt handling
    _custom_system_prompt = None
    if args.system_prompt:
        _custom_system_prompt = args.system_prompt
    elif args.system_prompt_file:
        try:
            with open(args.system_prompt_file, "r") as f:
                _custom_system_prompt = f.read()
        except Exception as e:
            _writeln(f"  {RED}Failed to read system prompt file: {e}{RESET}")

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

    # ── Banner (JARVIS exact layout) ──
    def render_banner(model, provider, cwd, session_name, cmd_count):
        """JARVIS exact layout — mascot left, info right."""
        tw = _tw()
        # JARVIS mascot (like Clawd but JARVIS-themed)
        mascot = [
            f"{CYAN} ▐▛███▜▌{RESET}",
            f"{CYAN}▝▜█████▛▘{RESET}",
            f"{CYAN}  ▘▘ ▝▝{RESET}",
        ]
        # Info lines (right of mascot)
        info = [
            f"  {BOLD}JARVIS v2.0{RESET}",
            f"  {model} · {provider}",
            f"  {cwd}",
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
        model_name = "server"
        provider_name = "localhost:8765"
        # Try to get actual model from server
        try:
            import urllib.request, json as _j
            resp = urllib.request.urlopen(f"{client._server_url}/api/providers", timeout=2)
            data = _j.loads(resp.read())
            provs = data.get("providers", [])
            if provs:
                model_name = provs[0].get("model", "server")
                provider_name = provs[0].get("name", "server")
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
        from src.commands_brain import registry as cmd_registry
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
                trusted = _json.loads(open(trust_file).read())
                return directory in trusted
        except Exception:
            pass
        return False

    def _trust_dir(directory):
        """Mark a directory as trusted."""
        import json as _json
        trusted = []
        try:
            if os.path.exists(trust_file):
                trusted = _json.loads(open(trust_file).read())
        except Exception:
            pass
        if directory not in trusted:
            trusted.append(directory)
        os.makedirs(os.path.dirname(trust_file), exist_ok=True)
        open(trust_file, "w").write(_json.dumps(trusted, indent=2))

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
        _writeln(f" {CYAN}❯{RESET} 1. Yes, I trust this folder")
        _writeln(f"   2. No, exit")
        _writeln()
        try:
            choice = input(f" {DIM}Enter to confirm · 2 to exit:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "2"
        if choice == "2":
            _writeln(f"  {DIM}Exiting. Run jarvis from a trusted directory.{RESET}")
            return
        _trust_dir(cwd)
        _writeln()

    # Clear screen — banner at TOP, input pinned at BOTTOM
    os.system("clear" if os.name != "nt" else "cls")

    def _exit_alt_screen():
        pass

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
    from src.shells.cli.companion import Companion
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

    # ── Simple Terminal Layout (no scroll regions, no absolute positioning) ──
    # Banner at top. Output flows down. Input drawn inline. Like JARVIS.
    INPUT_ZONE_HEIGHT = 4

    def _term_rows():
        try:
            return os.get_terminal_size().lines
        except OSError:
            return 24

    _frame_drawn = False

    def _setup_zones():
        pass

    def _teardown_zones():
        pass

    def _draw_input_frame(mode_prefix="", buf_text=""):
        """Draw the 4-line input frame inline at the current cursor position.

        Uses \\033[s to save the prompt cursor position. All other functions
        (menu, erase, search) navigate relative to the saved prompt via \\033[u].
        """
        nonlocal _frame_drawn
        tw = _tw()
        mode_str = brain.mode if client._is_full_brain else "normal"

        # Right side: effort + companion name
        right_parts = []
        try:
            effort = getattr(brain, 'reasoner', None)
            effort_val = getattr(effort, 'effort', 'high') if effort else 'high'
            if effort_val and effort_val != 'high':
                right_parts.append(f"● {effort_val}")
        except Exception:
            pass
        if _companion and _companion.enabled and hasattr(_companion, 'data') and _companion.data:
            cname = _companion.data.get("name", "")
            if cname:
                right_parts.append(cname)
        right_str = " · ".join(right_parts)

        sep = f"{DIM}{'─' * tw}{RESET}"
        prompt = f"{YELLOW}{mode_str}{RESET} ❯ " if mode_str != "normal" else "❯ "

        # Footer
        left = f"  {DIM}? for shortcuts{RESET}"
        if right_str:
            pad = max(1, tw - 16 - len(right_str) - 2)
            footer = f"{left}{' ' * pad}{DIM}{right_str}{RESET}"
        else:
            footer = left

        # Draw inline — clear from here and print 4 lines
        _write("\033[J")  # clear from cursor to end of screen
        _writeln(sep)
        _write(f"{prompt}{buf_text}")
        _write("\033[s")  # save cursor on prompt line
        _writeln()
        _writeln(sep)
        _write(footer)
        _write("\033[u")  # restore to prompt line
        _frame_drawn = True
        sys.stdout.flush()

    def _erase_frame():
        """Erase the input frame. Cursor returns to where the frame started."""
        nonlocal _frame_drawn
        if not _frame_drawn:
            return
        # Prompt is on line 2 of frame. Go up 1 to separator, clear to end.
        _write("\033[u\033[A\r\033[J")
        _frame_drawn = False

    def _output(text: str):
        """Print text. Erases frame first if needed, then writes inline."""
        _erase_frame()
        _write(text)
        sys.stdout.flush()

    def _outputln(text: str = ""):
        _output(text + "\n")

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
        cwd_display = os.getcwd().replace(os.path.expanduser("~"), "~")
        if session_mgr.current:
            session_name = session_mgr.current.name or session_mgr.current.display_name
        # Clear and redraw — banner at top
        os.system("clear" if os.name != "nt" else "cls")
        _frame_drawn = False
        _writeln(render_banner(model_name, provider_name, cwd_display, session_name, cmd_count))
        _writeln()

    # Handle terminal resize — full redraw including input frame
    import signal
    _in_input = False  # Track if we're waiting for input

    def _handle_resize(signum, frame):
        _redraw()
        _setup_zones()
        if _in_input:
            _draw_input_frame(_get_mode_prefix())

    try:
        signal.signal(signal.SIGWINCH, _handle_resize)
    except (AttributeError, ValueError):
        pass

    # ── Async input reader with slash command autocomplete ──
    async def _async_read_input(mode_prefix, tw):
        """Async input reader. Non-blocking so queries can stream concurrently.

        Returns the input string, or None on EOF.
        """
        import tty, termios

        # Load command list for autocomplete
        try:
            from src.commands_brain import registry as _reg
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
        # Merge: session messages + any new ones typed this session
        for h in _async_read_input._session_history:
            if h not in _history_entries:
                _history_entries.append(h)
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
            if hide_menu:
                _hide_menu()
            _erase_frame()
            _draw_input_frame(mode_prefix, text)

        MAX_VISIBLE = 10

        def _show_menu(matches):
            """Show autocomplete menu above the input frame."""
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

            # Position relative to prompt (\033[u). Separator is 1 above prompt.
            # Menu goes above that: total offset up = 1 + total_menu_lines.
            offset = 1 + total_menu_lines
            _write("\033[u")  # go to prompt
            _write(f"\033[{offset}A\r")  # up to menu top
            if start > 0:
                _write(f"\033[K    {DIM}↑ {start} more above{RESET}\n")
            for i in range(start, end):
                cmd = matches[i]
                pfx = f"  {CYAN}❯{RESET} " if i == selected else "    "
                # Show alias hint if match was via alias
                input_prefix = "".join(buf)[1:].lower()
                alias_hint = ""
                if input_prefix and not cmd.name.startswith(input_prefix):
                    for alias in (cmd.aliases or []):
                        if alias.lstrip("/").lower().startswith(input_prefix):
                            alias_hint = f" {DIM}(/{alias.lstrip('/')}){RESET}"
                            break
                desc = cmd.description[:tw - 50] if cmd.description else ""
                _write(f"\033[K{pfx}{CYAN}/{cmd.name:<25s}{RESET}{alias_hint} {DIM}{desc}{RESET}\n")
            if end < total:
                _write(f"\033[K    {DIM}↓ {total - end} more below{RESET}\n")
            menu_lines = total_menu_lines
            _write("\033[u")  # back to prompt
            menu_visible = True
            sys.stdout.flush()

        def _hide_menu():
            """Erase the autocomplete menu."""
            nonlocal menu_visible, menu_lines
            if not menu_visible:
                return
            offset = 1 + menu_lines
            _write("\033[u")
            _write(f"\033[{offset}A\r")
            for i in range(menu_lines):
                _write("\033[K\n")
            _write("\033[u")
            menu_visible = False
            menu_lines = 0
            sys.stdout.flush()

        def _get_matches():
            nonlocal all_cmds
            text = "".join(buf)
            if not text.startswith("/"):
                return []
            # Reload if commands weren't available at init (e.g., lazy registration)
            if not all_cmds:
                try:
                    from src.commands_brain import registry as _reg2
                    all_cmds = sorted(_reg2.list_commands(include_hidden=False), key=lambda c: c.name)
                except Exception:
                    pass
            prefix = text[1:].lower()
            if not prefix:
                return list(all_cmds)
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

        def _draw_search_prompt():
            """Draw the Ctrl+R search prompt in the input zone."""
            query = "".join(_search_buf)
            matches = _get_search_matches()
            match_text = ""
            if matches and _search_match_idx < len(matches):
                match_text = matches[_search_match_idx]
                max_len = _tw() - 4
                if len(match_text) > max_len:
                    match_text = match_text[:max_len - 3] + "..."
                match_text = match_text.replace("\n", " ")
            # Redraw the frame area with search prompt. Use relative positioning.
            _write("\033[u\033[A\r")  # prompt -> up to separator
            _write(f"\033[K{DIM}{'─' * _tw()}{RESET}\n")
            _write(f"\033[K{YELLOW}(reverse-i-search){RESET}: {query}{DIM} -> {match_text}{RESET}\n")
            _write(f"\033[K{DIM}{'─' * _tw()}{RESET}\n")
            _write(f"\033[K  {DIM}Ctrl+R next | Enter accept | Esc cancel{RESET}")
            # Position cursor on search input line
            _write("\033[u")
            cursor_col = len("(reverse-i-search): ") + len(query) + 1
            _write(f"\r\033[{cursor_col - 1}C")
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

            # ── Normal input mode ──
            if ch == "\n" or ch == "\r":
                if menu_visible:
                    matches = _get_matches()
                    if matches and selected < len(matches):
                        buf.clear()
                        buf.extend(f"/{matches[selected].name}")
                _hide_menu()
                text = "".join(buf).strip()
                if text and (not _history_entries or _history_entries[-1] != text):
                    _history_entries.append(text)
                    # Persist to session-level accumulator
                    if hasattr(_async_read_input, '_session_history'):
                        _async_read_input._session_history.append(text)
                _history_idx = len(_history_entries)
                result_future.set_result(text)
            elif ch == "\x04":
                _hide_menu()
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
                result_future.set_result("")
            elif ch == "\x0c":
                # Ctrl+L: Clear and redraw screen (full redraw, not just input)
                _hide_menu()
                _erase_frame()
                os.system("clear" if os.name != "nt" else "cls")
                _frame_drawn = False
                _writeln(render_banner(model_name, provider_name, cwd_display, session_name, cmd_count))
                _writeln()
                _setup_zones()
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
                with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
                    tf.write("".join(buf))
                    tf_path = tf.name
                try:
                    import termios as _termios
                    _termios.tcsetattr(fd, _termios.TCSADRAIN, old_settings)
                    _write("\033[r")  # Reset scroll region for editor
                    os.system(f"{editor} {tf_path}")
                    import tty as _tty
                    _tty.setcbreak(fd)
                    with open(tf_path, "r") as f:
                        new_text = f.read().strip()
                    buf.clear()
                    buf.extend(new_text)
                except Exception:
                    pass
                finally:
                    try:
                        os.unlink(tf_path)
                    except OSError:
                        pass
                _setup_zones()
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
                        buf.clear()
                        buf.extend(f"/{matches[selected].name} ")
                        _redraw()
                        _hide_menu()
            elif ch >= " ":
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

        def _handle_escape_seq():
            """Process buffered escape sequence after timeout."""
            nonlocal selected, _esc_buf, _esc_timer
            nonlocal _history_idx, _saved_buf
            nonlocal _search_mode, _search_buf, _search_match_idx
            _esc_timer = None
            seq = "".join(_esc_buf)  # e.g. "[A" for up arrow
            _esc_buf.clear()

            # In search mode, Escape cancels
            if _search_mode and seq == "":
                _search_mode = False
                _search_buf.clear()
                _search_match_idx = 0
                _redraw()
                return

            if seq == "[A" and menu_visible:
                matches = _get_matches()
                selected = max(0, selected - 1)
                _show_menu(matches)
            elif seq == "[B" and menu_visible:
                matches = _get_matches()
                selected = min(len(matches) - 1, selected + 1)
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
            else:
                # Just Escape — close menu
                _hide_menu()

        def _on_stdin():
            nonlocal _esc_buf, _esc_timer
            if result_future.done():
                return
            try:
                data = os.read(fd, 32).decode("utf-8", errors="replace")
            except OSError:
                return

            for ch in data:
                if _esc_timer is not None or len(_esc_buf) > 0:
                    # We're in an escape sequence (after \x1b)
                    _esc_buf.append(ch)
                    # Arrow keys: \x1b [ A/B/C/D — need exactly "[" + letter
                    if len(_esc_buf) >= 2 and _esc_buf[0] == "[" and _esc_buf[-1].isalpha():
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
                    elif len(_esc_buf) > 6:
                        # Too long — something went wrong, flush
                        if _esc_timer:
                            _esc_timer.cancel()
                            _esc_timer = None
                        _esc_buf.clear()
                elif ch == "\x1b":
                    _esc_buf.clear()
                    # Wait briefly for rest of sequence
                    _esc_timer = loop.call_later(0.05, _handle_escape_seq)
                else:
                    _process_char(ch)

        try:
            tty.setcbreak(fd)
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
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    # ── Background Query Runner ──
    async def _run_query(user_input, voice_mode=False):
        """Run a query as a background task, outputting to the scroll region."""
        nonlocal _active_task

        from src.shells.cli.display import (
            tool_call_line, tool_result_line, tool_result_preview,
            diff_display, token_footer as _token_footer,
        )

        start = time.time()
        session_mgr.add_message("user", user_input)

        full_text = ""
        tool_count = 0
        _streaming_text = False
        _tool_states = []
        _tokens_this_turn = 0

        # Async status dot — JARVIS style (● blinks while working)
        _spin_task = None
        _spin_label = ["Thinking..."]

        async def _spin_loop():
            i = 0
            t0 = time.time()
            while True:
                elapsed = time.time() - t0
                # Blink: alternate between visible and dim ●
                dot = f"{CYAN}●{RESET}" if i % 2 == 0 else f"{DIM}●{RESET}"
                _output(f"\r  {dot} {DIM}{_spin_label[0]}{RESET}\033[K")
                i += 1
                await asyncio.sleep(0.4)

        def _start_spin(label="Thinking..."):
            nonlocal _spin_task
            _stop_spin()
            _spin_label[0] = label
            _spin_task = asyncio.get_event_loop().create_task(_spin_loop())

        def _stop_spin():
            nonlocal _spin_task
            if _spin_task and not _spin_task.done():
                _spin_task.cancel()
                _spin_task = None
                _output("\r\033[K")

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

                elif etype == "text":
                    chunk = event.get("content", "")
                    if not chunk:
                        continue
                    if not _streaming_text:
                        _stop_spin()
                        _streaming_text = True
                        _outputln()
                    full_text += chunk
                    _output(chunk)

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
            full_text = ""
            _outputln(f"\n  {DIM}Cancelled.{RESET}")
        except Exception as e:
            full_text = f"Error: {str(e)[:80]}"
        finally:
            _stop_spin()

        if _streaming_text:
            _outputln()
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
            _outputln(render_markdown(full_text.strip()))
            _outputln()

        # Token footer with cost
        elapsed_total = time.time() - start
        parts = []
        if _tokens_this_turn > 0:
            if _tokens_this_turn >= 1000:
                parts.append(f"{_tokens_this_turn / 1000:.1f}K tokens")
            else:
                parts.append(f"{_tokens_this_turn} tokens")
        if tool_count > 0:
            parts.append(f"{tool_count} tool{'s' if tool_count > 1 else ''}")
        parts.append(f"{elapsed_total:.1f}s")

        # Add cost
        try:
            from src.agent.cost_tracker import get_tracker
            tracker = get_tracker()
            cost = tracker.get_session_cost()
            if cost > 0.001:
                parts.append(f"${cost:.2f}")
        except Exception:
            pass

        if parts:
            _outputln(f"  {DIM}{' · '.join(parts)}{RESET}")
        if full_text.strip() and tool_count > 0:
            _buddy_says("success")
        _outputln()

        if full_text.strip():
            session_mgr.add_message("jarvis", full_text)

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
                continue
            _cancelled = False
            _voice_mode = False

            # Echo user input in output zone — subtle, like JARVIS
            _outputln()
            _outputln(f"  {user_input}")
            _outputln()

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
                        from src.commands_brain import registry as _reg
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

                # CLI-only shortcuts
                if cmd_name == "visual" and cmd_args:
                    subprocess.Popen(
                        ["x-terminal-emulator", "-e", f"bash -c '{cmd_args}; echo; echo [DONE]; read'"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                    )
                    continue

                # In server mode, forward commands to server via think_stream
                if not client._server_mode:
                    # Dispatch through CommandRegistry (local brain)
                    if client._is_full_brain and hasattr(brain, "dispatch_command"):
                        result = await brain.dispatch_command(cmd_name, cmd_args, session_mgr=session_mgr)
                    else:
                        try:
                            from src.commands_brain import registry as cmd_registry
                            from src.commands_brain.registry import CommandContext
                            ctx = CommandContext(
                                brain=brain if client._is_full_brain else None,
                                session_mgr=session_mgr, raw_input=user_input,
                                args=cmd_args, mode=brain.mode if client._is_full_brain else "normal",
                            )
                            result = await cmd_registry.dispatch(cmd_name, ctx)
                        except Exception:
                            result = None

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
                            _setup_zones()
                        elif result.text:
                            _outputln()
                            _outputln(result.text)
                            _outputln()
                    else:
                        # Unknown command — try fuzzy suggestion
                        try:
                            from src.commands_brain import registry as cmd_registry
                            suggestions = cmd_registry.suggest(cmd_name, limit=3)
                            if suggestions:
                                names = ", ".join(f"/{s.name}" for s in suggestions)
                                _outputln(f"  {DIM}Unknown command: /{cmd_name}. Did you mean: {names}?{RESET}")
                            else:
                                _outputln(f"  {DIM}Unknown command: /{cmd_name}. Type /help for commands.{RESET}")
                        except Exception:
                            _outputln(f"  {DIM}Unknown command: /{cmd_name}{RESET}")
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
            _outputln(f"  {RED}✘ {e}{RESET}\n")


def run():
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except RuntimeError:
        pass
    finally:
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
