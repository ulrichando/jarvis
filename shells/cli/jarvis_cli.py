#!/usr/bin/env python3
"""JARVIS CLI — autonomous AI agent in your terminal.

Modeled after Claude Code's terminal UX:
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

# ── Minimal terminal output (no Rich dependency for core rendering) ──

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
GREY = "\033[90m"
WHITE = "\033[97m"
BG_DARK = "\033[48;5;236m"

# Braille spinner frames (same as Claude Code)
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
            output.append(f"  {BG_DARK}  {line}  {RESET}")
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
            from brain.main import Brain
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
    parser.add_argument("query", nargs="*", help="Initial query")
    return parser.parse_args()


async def main():
    args = parse_args()

    # MCP server mode
    if args.serve:
        from brain.mcp.server import MCPServer
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
    from brain.sessions import SessionManager
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
        query = args.print_mode
        if stdin_data:
            query = f"{query}\n\n{stdin_data}"
        session_mgr.add_message("user", query)
        full_response = ""
        async for event in client.query_stream(query):
            t = event.get("type", "")
            if t == "text":
                chunk = event.get("content", "")
                full_response += chunk
                sys.stdout.write(chunk)
                sys.stdout.flush()
            elif t == "tool_call":
                name = event.get("name", "")
                sys.stderr.write(f"  {name}\n")
            elif t == "done":
                break
        if full_response:
            print()
        session_mgr.add_message("jarvis", full_response)
        session_mgr.save_current()
        await client.close()
        session_mgr.close()
        return

    # ── Banner (Claude Code layout with JARVIS branding) ──
    def render_banner(model, provider, cwd, session_name, cmd_count):
        """Claude Code-style layout with JARVIS HUD logo."""
        logo = [
            f"{CYAN}  ╔═▓▓▓▓═╗{RESET}   {BOLD}JARVIS v2.0{RESET}",
            f"{CYAN}  ║ {BOLD}J.A.R.V.I.S{RESET}{CYAN} ║{RESET}  {DIM}{model} · {provider}{RESET}",
            f"{CYAN}  ╚═▓▓▓▓═╝{RESET}   {DIM}{cwd}{RESET}",
        ]
        return "\n".join(logo)

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
        from brain.commands import registry as cmd_registry
        cmd_count = cmd_registry.visible_count
    except Exception:
        pass

    # ── Workspace Trust Prompt (like Claude Code) ──
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

    # Clear screen and draw banner
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
    _writeln()

    # Initialize companion
    from shells.cli.companion import Companion
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

    # ── Scroll Region Management ──
    # Split terminal: output scrolls in top zone, input stays pinned at bottom.
    INPUT_ZONE_HEIGHT = 4  # top bar, input line, bottom bar, hints

    def _term_rows():
        try:
            return os.get_terminal_size().lines
        except OSError:
            return 24

    def _setup_zones():
        """Set scroll region to exclude bottom input zone."""
        rows = _term_rows()
        output_bottom = rows - INPUT_ZONE_HEIGHT
        if output_bottom < 2:
            output_bottom = 2
        _write(f"\033[1;{output_bottom}r")  # Set scroll region
        _write(f"\033[{output_bottom};1H")  # Move to bottom of output zone

    def _teardown_zones():
        """Reset scroll region to full terminal."""
        _write("\033[r")

    def _draw_input_frame(mode_prefix="", buf_text=""):
        """Draw input frame in the pinned bottom zone."""
        rows = _term_rows()
        tw = _tw()
        input_top = rows - INPUT_ZONE_HEIGHT + 1
        mode_str = brain.mode if client._is_full_brain else "normal"
        status_right = f"◐ {mode_str}" if mode_str != "normal" else ""

        # Move to fixed input zone (below scroll region)
        _write(f"\033[{input_top};1H")
        _write(f"\033[K{DIM}{'─' * tw}{RESET}\n")
        _write(f"\033[K{mode_prefix}{CYAN}❯{RESET} {buf_text}\n")
        _write(f"\033[K{DIM}{'─' * tw}{RESET}\n")
        hint = f"  {DIM}? for shortcuts{RESET}"
        if status_right:
            pad = max(1, tw - 16 - len(status_right) - 2)
            hint += " " * pad + f"{DIM}{status_right}{RESET}"
        _write(f"\033[K{hint}")
        # Position cursor on input line
        prompt_vis_len = len(mode_prefix.replace(YELLOW, "").replace(RESET, "")) + 2  # "❯ "
        cursor_col = prompt_vis_len + len(buf_text) + 1
        _write(f"\033[{input_top + 1};{cursor_col}H")
        sys.stdout.flush()

    def _output(text: str):
        """Write to the output zone (scroll region), then restore cursor to input."""
        _write(f"\033[s")  # Save cursor position
        rows = _term_rows()
        output_bottom = rows - INPUT_ZONE_HEIGHT
        _write(f"\033[{output_bottom};1H")  # Move to bottom of scroll region
        _write(text)
        _write(f"\033[u")  # Restore cursor
        sys.stdout.flush()

    def _outputln(text: str = ""):
        """Write line to the output zone."""
        _output(text + "\n")

    # Helper to full redraw (used by /clear and resize)
    def _redraw():
        nonlocal model_name, provider_name, cwd_display, session_name, cmd_count
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
        # Clear and redraw from top
        os.system("clear" if os.name != "nt" else "cls")
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
            from brain.commands import registry as _reg
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

        # Escape sequence state
        _esc_buf = []
        _esc_timer = None

        def _redraw():
            """Redraw input line in the fixed zone."""
            text = "".join(buf)
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

            rows = _term_rows()
            # Draw menu just above input zone, in the output area
            menu_top = rows - INPUT_ZONE_HEIGHT - (end - start) - (1 if start > 0 else 0) - (1 if end < total else 0)
            if menu_top < 1:
                menu_top = 1
            _write(f"\033[s")  # save cursor
            line = menu_top
            if start > 0:
                _write(f"\033[{line};1H\033[K    {DIM}↑ {start} more above{RESET}")
                line += 1
            for i in range(start, end):
                cmd = matches[i]
                prefix = f"  {CYAN}❯{RESET} " if i == selected else "    "
                desc = cmd.description[:tw - 45] if cmd.description else ""
                _write(f"\033[{line};1H\033[K{prefix}{CYAN}/{cmd.name:<25s}{RESET} {DIM}{desc}{RESET}")
                line += 1
            if end < total:
                _write(f"\033[{line};1H\033[K    {DIM}↓ {total - end} more below{RESET}")
                line += 1
            menu_lines = line - menu_top
            _write(f"\033[u")  # restore cursor
            menu_visible = True
            sys.stdout.flush()

        def _hide_menu():
            """Erase the autocomplete menu."""
            nonlocal menu_visible, menu_lines
            if not menu_visible:
                return
            rows = _term_rows()
            menu_top = rows - INPUT_ZONE_HEIGHT - menu_lines
            if menu_top < 1:
                menu_top = 1
            _write(f"\033[s")
            for i in range(menu_lines):
                _write(f"\033[{menu_top + i};1H\033[K")
            _write(f"\033[u")
            menu_visible = False
            menu_lines = 0
            sys.stdout.flush()

        def _get_matches():
            text = "".join(buf)
            if not text.startswith("/"):
                return []
            prefix = text[1:].lower()
            return [c for c in all_cmds if c.name.startswith(prefix)]

        def _process_char(ch):
            nonlocal selected, _esc_buf, _esc_timer
            if result_future.done():
                return

            if ch == "\n" or ch == "\r":
                if menu_visible:
                    matches = _get_matches()
                    if matches and selected < len(matches):
                        buf.clear()
                        buf.extend(f"/{matches[selected].name}")
                _hide_menu()
                result_future.set_result("".join(buf).strip())
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
                _redraw()
                result_future.set_result("")
            elif ch == "\x7f" or ch == "\x08":
                if buf:
                    buf.pop()
                    _redraw()
                    if "".join(buf).startswith("/"):
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
                _redraw()
                if "".join(buf).startswith("/"):
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
            _esc_timer = None
            seq = "".join(_esc_buf)  # e.g. "[A" for up arrow
            _esc_buf.clear()

            if seq == "[A" and menu_visible:
                matches = _get_matches()
                selected = max(0, selected - 1)
                _show_menu(matches)
            elif seq == "[B" and menu_visible:
                matches = _get_matches()
                selected = min(len(matches) - 1, selected + 1)
                _show_menu(matches)
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
                if len(_esc_buf) > 0:
                    # We're in an escape sequence (after \x1b)
                    _esc_buf.append(ch)
                    if len(_esc_buf) >= 2:
                        # Got full sequence like "[A", "[B"
                        if _esc_timer:
                            _esc_timer.cancel()
                            _esc_timer = None
                        _handle_escape_seq()
                elif ch == "\x1b":
                    _esc_buf.clear()  # Start fresh — don't include \x1b itself
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

        from shells.cli.display import (
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

        # Async spinner (no threads — writes to output zone)
        _spin_task = None
        _spin_label = ["Thinking..."]

        async def _spin_loop():
            i = 0
            t0 = time.time()
            while True:
                frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
                elapsed = time.time() - t0
                _output(f"\r  {DIM}{frame} {_spin_label[0]} ({elapsed:.1f}s){RESET}\033[K")
                i += 1
                await asyncio.sleep(0.12)

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
                    tool_count += 1
                    name = event.get("name", "")
                    args = event.get("args", {})
                    _tool_states.append({
                        "name": name, "args": args,
                        "start": time.time(), "lines": [], "error": False,
                    })
                    _outputln(tool_call_line(name, args))
                    _start_spin(name)

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
            _outputln(f"{CYAN}●{RESET} {render_markdown(full_text.strip())}")
            _outputln()

        # Token footer
        elapsed_total = time.time() - start
        if _tokens_this_turn > 0 or tool_count > 0:
            _outputln(_token_footer(_tokens_this_turn, tool_count, elapsed_total))
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

            # Show the submitted command in the output zone
            _outputln(f"{DIM}{'─' * _tw()}{RESET}")
            _outputln(f"{_get_mode_prefix()}{CYAN}❯{RESET} {user_input}")
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
                            from brain.speech.stt import transcribe_audio
                            text = transcribe_audio(audio, 16000)
                    else:
                        from brain.speech.stt import transcribe_audio
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

            # ═══ ? SHORTCUT HELP (Claude Code style) ═══
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
                        ("Ctrl+D", "Exit (press twice)"),
                        ("Ctrl+C", "Cancel current operation"),
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

                # Just "/" alone — show command menu with descriptions (Claude Code style)
                if not cmd_name:
                    try:
                        from brain.commands import registry as _reg
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
                            from brain.commands import registry as cmd_registry
                            from brain.commands.registry import CommandContext
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
                            from brain.commands import registry as cmd_registry
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
