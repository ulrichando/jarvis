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
    def __init__(self):
        self.brain = None
        self._is_full_brain = False

    async def connect(self) -> bool:
        # Suppress ALL library logging during brain init (prevents terminal noise)
        prev_level = logging.root.level
        logging.disable(logging.WARNING)
        import warnings
        warnings.filterwarnings("ignore")

        # Try full Brain first (91 commands, MCP, agents, etc.)
        try:
            from brain.main import Brain
            self.brain = Brain(quiet=True)
            self._is_full_brain = True
            logging.disable(logging.NOTSET)  # Restore after init
            logging.root.setLevel(prev_level)
            return True
        except Exception as e:
            pass  # Silent fallback

        # Fallback to lightweight CogScript brain
        try:
            from brain.cogscript.brain_adapter import CogScriptBrain
            self.brain = CogScriptBrain()
            await self.brain.start()
            logging.disable(logging.NOTSET)
            logging.root.setLevel(prev_level)
            return True
        except Exception as e:
            logging.disable(logging.NOTSET)
            logging.root.setLevel(prev_level)
            _writeln(f"  {RED}Brain failed: {e}{RESET}")
            return False

    async def query(self, text: str) -> str:
        return await self.brain.think(text)

    async def query_stream(self, text: str):
        if self._is_full_brain and hasattr(self.brain, "think_stream"):
            async for event in self.brain.think_stream(text):
                yield event
        else:
            response = await self.brain.think(text)
            yield {"type": "text", "content": response}
            yield {"type": "done", "content": response}

    async def close(self):
        if self.brain:
            if self._is_full_brain:
                if hasattr(self.brain, "mcp"):
                    self.brain.mcp.stop_all()
                if hasattr(self.brain, "memory"):
                    self.brain.memory.save()
            elif hasattr(self.brain, "shutdown"):
                await self.brain.shutdown()


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

    # Get model/provider from registry (not active_model which is "none" before first query)
    model_name = "local"
    provider_name = "local"
    if client._is_full_brain and hasattr(brain, "reasoner"):
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
        if _in_input:
            # Redraw the input frame at new width
            tw = _tw()
            mode_str = brain.mode if client._is_full_brain else "normal"
            mode_prefix = f"{YELLOW}{mode_str}{RESET} " if mode_str != "normal" else ""
            status_right = f"◐ {mode_str}" if mode_str != "normal" else ""

            _writeln(f"{DIM}{'─' * tw}{RESET}")
            _writeln()
            _writeln(f"{DIM}{'─' * tw}{RESET}")
            hint = f"  {DIM}? for shortcuts{RESET}"
            if status_right:
                pad = max(1, tw - 16 - len(status_right) - 2)
                hint += " " * pad + f"{DIM}{status_right}{RESET}"
            _write(hint)
            _write(f"\033[2A\r{mode_prefix}{CYAN}❯{RESET} ")
            sys.stdout.flush()

    try:
        signal.signal(signal.SIGWINCH, _handle_resize)
    except (AttributeError, ValueError):
        pass

    # ── Main REPL Loop ──
    while True:
        try:
            if initial_query:
                user_input = initial_query
                initial_query = ""
            else:
                mode_prefix = ""
                if client._is_full_brain and brain.mode != "normal":
                    mode_prefix = f"{YELLOW}{brain.mode}{RESET} "
                try:
                    # Claude Code-style bordered input with status bar
                    tw = _tw()
                    mode_str = brain.mode if client._is_full_brain else "normal"
                    status_right = f"◐ {mode_str}" if mode_str != "normal" else ""

                    # Print all 4 lines: top bar, input line, bottom bar, hint+status
                    _writeln(f"{DIM}{'─' * tw}{RESET}")
                    _writeln()  # Empty line for input
                    _writeln(f"{DIM}{'─' * tw}{RESET}")
                    hint = f"  {DIM}? for shortcuts{RESET}"
                    if status_right:
                        pad = max(1, tw - 16 - len(status_right) - 2)
                        hint += " " * pad + f"{DIM}{status_right}{RESET}"
                    _write(hint)

                    # Move cursor back up to the input line (line 2)
                    _write(f"\033[2A\r{mode_prefix}{CYAN}❯{RESET} ")
                    sys.stdout.flush()

                    _in_input = True
                    raw = sys.stdin.readline()
                    _in_input = False
                    if not raw:
                        raise EOFError
                    user_input = raw.strip()

                    # Move cursor down past the frame
                    _write(f"\033[2B\r\n")

                    if not user_input:
                        # Clear the prompt frame on empty input
                        _write(f"\033[4A\033[J")
                        continue
                except EOFError:
                    # First Ctrl+D: show confirmation (like Claude Code)
                    _writeln()
                    _writeln(f"  {DIM}Press Ctrl+D again to exit, or keep typing.{RESET}")
                    try:
                        raw = sys.stdin.readline()
                        if not raw:
                            # Second Ctrl+D: actually exit
                            raise EOFError
                        user_input = raw.strip()
                        if user_input:
                            pass  # Fall through to process input
                        else:
                            continue
                    except EOFError:
                        break

            if not user_input:
                continue
            _cancelled = False

            # ═══ VOICE INPUT ═══
            if user_input in ("v", "/voice", "/speak", "/mic", "/listen"):
                try:
                    import sounddevice as sd
                    import numpy as np
                    _writeln(f"  {CYAN}🎤 Listening...{RESET} (speak now, 5 seconds)")
                    audio = sd.rec(int(5 * 16000), samplerate=16000, channels=1, dtype='float32')
                    sd.wait()
                    audio = audio.flatten()
                    rms = float(np.sqrt(np.mean(audio ** 2)))
                    if rms < 0.001:
                        _writeln(f"  {DIM}No speech detected.{RESET}")
                        continue
                    _writeln(f"  {DIM}Transcribing...{RESET}")
                    from brain.speech.stt import transcribe_audio
                    text = transcribe_audio(audio, 16000)
                    if text:
                        _writeln(f"  {GREEN}You said:{RESET} {text}")
                        user_input = text
                        # Fall through to process the transcribed text
                    else:
                        _writeln(f"  {DIM}Couldn't make that out. Try again.{RESET}")
                        continue
                except ImportError as e:
                    _writeln(f"  {RED}Voice needs: pip install faster-whisper sounddevice numpy{RESET}")
                    continue
                except Exception as e:
                    _writeln(f"  {RED}Voice error: {e}{RESET}")
                    continue

            # ═══ ? SHORTCUT HELP (Claude Code style) ═══
            if user_input == "?":
                _writeln()
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
                    _writeln(f"  {BOLD}{section}{RESET}")
                    for key, desc in items:
                        _writeln(f"    {CYAN}{key:<14s}{RESET} {DIM}{desc}{RESET}")
                    _writeln()
                continue

            # ═══ SLASH COMMANDS ═══
            if user_input.startswith("/"):
                parts = user_input[1:].split(None, 1)
                cmd_name = parts[0] if parts else ""
                cmd_args = parts[1] if len(parts) > 1 else ""

                # Just "/" alone — show command menu (like Claude Code)
                if not cmd_name:
                    try:
                        from brain.commands import registry as _reg
                        from brain.commands.registry import CATEGORIES
                        _writeln()
                        for cat_slug, cat_name in CATEGORIES:
                            cmds = _reg.list_commands(category=cat_slug)
                            if not cmds:
                                continue
                            _writeln(f"  {BOLD}{cat_name}{RESET}")
                            row = []
                            for cmd in cmds:
                                row.append(f"{CYAN}/{cmd.name}{RESET}")
                                if len(row) >= 5:
                                    _writeln(f"    {'  '.join(row)}")
                                    row = []
                            if row:
                                _writeln(f"    {'  '.join(row)}")
                            _writeln()
                        _writeln(f"  {DIM}Type /command to run. /help for details.{RESET}")
                        _writeln()
                    except Exception:
                        _writeln(f"  {DIM}Type /help for all commands.{RESET}")
                    continue

                # CLI-only shortcuts
                if cmd_name == "visual" and cmd_args:
                    subprocess.Popen(
                        ["x-terminal-emulator", "-e", f"bash -c '{cmd_args}; echo; echo [DONE]; read'"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                    )
                    continue

                # Dispatch through CommandRegistry
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
                        session_mgr.save_current()
                        await client.close()
                        session_mgr.close()
                        _exit_alt_screen()
                        print("Session saved. JARVIS offline.")
                        return
                    elif result.action == "clear":
                        _redraw()
                    elif result.text:
                        _writeln()
                        _writeln(result.text)
                        _writeln()
                else:
                    # Unknown command — try fuzzy suggestion
                    try:
                        from brain.commands import registry as cmd_registry
                        suggestions = cmd_registry.suggest(cmd_name, limit=3)
                        if suggestions:
                            names = ", ".join(f"/{s.name}" for s in suggestions)
                            _writeln(f"  {DIM}Unknown command: /{cmd_name}. Did you mean: {names}?{RESET}")
                        else:
                            _writeln(f"  {DIM}Unknown command: /{cmd_name}. Type /help for commands.{RESET}")
                    except Exception:
                        _writeln(f"  {DIM}Unknown command: /{cmd_name}{RESET}")
                continue

            # ═══ SHELL SHORTCUT: !command ═══
            if user_input.startswith("!"):
                cmd = user_input[1:].strip()
                if not cmd:
                    continue
                analyze = cmd.startswith("!")
                if analyze:
                    cmd = cmd[1:].strip()

                _writeln(f"  {DIM}$ {cmd}{RESET}")
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
                        _writeln(render_markdown(rendered))

                session_mgr.add_message("user", f"!{cmd}")
                session_mgr.add_message("jarvis", output[:500])

                if analyze and output.strip():
                    spinner.tick("Analyzing output...")
                    analysis = await client.query(f"Analyze this output:\n{output[:2000]}")
                    spinner.done("Analysis complete")
                    _writeln(render_markdown(analysis))
                    session_mgr.add_message("jarvis", analysis)
                continue

            # ═══ MAIN QUERY ═══
            start = time.time()
            session_mgr.add_message("user", user_input)

            full_text = ""
            tool_count = 0
            thinking_lines = 0

            # ── Event-driven display (Claude Code style — clean, minimal) ──
            from shells.cli.display import (
                tool_call_line, tool_result_line, tool_result_preview,
                diff_display, token_footer as _token_footer,
            )

            _spinner_stop = threading.Event()
            _spinner_label = "Thinking..."
            _spinner_start = time.time()
            _streaming_text = False
            _tool_states = []
            _tokens_this_turn = 0
            _auto_allow = set()

            def _spin():
                frames = SPINNER_FRAMES
                i = 0
                wrote_anything = False
                while not _spinner_stop.is_set():
                    frame = frames[i % len(frames)]
                    elapsed = time.time() - _spinner_start
                    if wrote_anything:
                        _write(f"\r  {DIM}{frame} {_spinner_label} ({elapsed:.1f}s){RESET}\033[K")
                    else:
                        _write(f"  {DIM}{frame} {_spinner_label} ({elapsed:.1f}s){RESET}")
                        wrote_anything = True
                    i += 1
                    _spinner_stop.wait(0.12)
                # Clean up: erase the spinner line completely
                _write("\r\033[K")

            def _stop_spinner():
                """Stop spinner and wait for thread to fully exit."""
                _spinner_stop.set()
                spinner_thread.join(timeout=1.0)

            def _start_spinner(label="Thinking..."):
                """Start a new spinner with given label."""
                nonlocal spinner_thread, _spinner_stop, _spinner_label, _spinner_start
                _spinner_stop = threading.Event()
                _spinner_label = label
                _spinner_start = time.time()
                spinner_thread = threading.Thread(target=_spin, daemon=True)
                spinner_thread.start()

            spinner_thread = threading.Thread(target=_spin, daemon=True)
            spinner_thread.start()

            try:
                async for event in client.query_stream(user_input):
                    if _cancelled:
                        break

                    etype = event.get("type", "")

                    if etype == "tool_call":
                        _stop_spinner()
                        if _streaming_text:
                            _writeln()  # End the text line before showing tool
                        tool_count += 1
                        name = event.get("name", "")
                        args = event.get("args", {})
                        _tool_states.append({
                            "name": name, "args": args,
                            "start": time.time(), "lines": [], "error": False,
                        })
                        _writeln(tool_call_line(name, args))
                        _start_spinner(name)

                    elif etype == "tool_result":
                        _stop_spinner()
                        result_text = event.get("content", event.get("result", ""))
                        name = event.get("name", "")
                        if _tool_states:
                            ts = _tool_states[-1]
                            elapsed_tool = time.time() - ts["start"]
                            is_error = (result_text.startswith("Error") or
                                        result_text.startswith("BLOCKED"))
                            ts["error"] = is_error
                            ts["lines"] = result_text.strip().split("\n") if result_text.strip() else []
                            _writeln(tool_result_line(name, result_text, not is_error, elapsed_tool))
                            if name == "edit_file" and ts["args"].get("old_string"):
                                diff = diff_display(
                                    ts["args"]["old_string"],
                                    ts["args"].get("new_string", ""),
                                    ts["args"].get("path", ""),
                                )
                                if diff:
                                    _writeln(diff)
                        _start_spinner("Thinking...")

                    elif etype == "text":
                        chunk = event.get("content", "")
                        if not chunk:
                            continue
                        if not _streaming_text:
                            _stop_spinner()
                            time.sleep(0.05)  # Let spinner thread fully die
                            _write("\r\033[K")  # Ensure clean line
                            _streaming_text = True
                            _writeln()  # Blank line before response
                        full_text += chunk
                        _write(chunk)

                    elif etype == "usage":
                        _tokens_this_turn += event.get("input_tokens", 0) + event.get("output_tokens", 0)

                    elif etype == "error":
                        err = event.get("content", "Error")
                        if "rate_limit" in err or "413" in err or "too large" in err:
                            full_text = "Give me a moment — rate limited. Try again."
                        elif "No provider" in err:
                            full_text = "No AI provider available. Check /doctor."

                    elif etype == "done":
                        pass  # Handled after loop

            except asyncio.CancelledError:
                full_text = ""
            except Exception as e:
                full_text = f"Error: {str(e)[:80]}"
            finally:
                _stop_spinner()

            # If we were streaming text, add newline after streamed content
            if _streaming_text:
                _writeln()
                _writeln()
            else:
                # Erase spinner line if no text was streamed
                _clear_line()

            # Filter out training data leaks and garbage
            if full_text:
                garbage_markers = ["<｜begin", "<|begin", "\\boxed{", "\\frac{", "\\sqrt{",
                                   "begin▁of▁sentence", "Question: How do you solve"]
                if any(m in full_text for m in garbage_markers):
                    full_text = "Sorry, I got confused there. Could you rephrase that?"

            # Show rendered response (if not already streamed, render markdown)
            if full_text.strip() and not _streaming_text:
                _writeln(f"{CYAN}●{RESET} {render_markdown(full_text.strip())}")
                _writeln()

            # Token footer (clean, dimmed — like Claude Code)
            elapsed_total = time.time() - start
            if _tokens_this_turn > 0 or tool_count > 0:
                _writeln(_token_footer(_tokens_this_turn, tool_count, elapsed_total))
            _writeln()

            if full_text.strip():
                session_mgr.add_message("jarvis", full_text)

            # ── Interactive fix approval (after troubleshoot) ──
            if (client._is_full_brain and hasattr(brain, '_pending_fixes')
                    and brain._pending_fixes):
                fixes = brain._pending_fixes
                _writeln(f"  {CYAN}Found {len(fixes)} auto-fixable issues.{RESET}")
                _writeln(f"  {DIM}Review each fix: (y)es / (n)o / (a)ll / (q)uit{RESET}")
                _writeln()

                applied = 0
                skipped = 0
                accept_all = False

                for i, fix in enumerate(fixes, 1):
                    rel = os.path.relpath(fix["file"], os.getcwd())
                    _writeln(f"  {BOLD}Fix {i}/{len(fixes)}{RESET}: {rel}:{fix['line']}")
                    _writeln(f"    {fix['description']}")
                    if fix.get("old"):
                        _writeln(f"    {RED}- {fix['old'].strip()}{RESET}")
                    if fix.get("new") is not None and not fix.get("delete_line"):
                        _writeln(f"    {GREEN}+ {fix['new'].strip()}{RESET}")
                    elif fix.get("delete_line"):
                        _writeln(f"    {DIM}(delete line){RESET}")

                    if accept_all:
                        choice = "y"
                    else:
                        try:
                            choice = input(f"    {CYAN}Apply? (y/n/a/q):{RESET} ").strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            choice = "q"

                    if choice == "q":
                        _writeln(f"  {DIM}Stopped. {applied} applied, {len(fixes)-i+1-skipped} remaining.{RESET}")
                        break
                    elif choice == "a":
                        accept_all = True
                        choice = "y"

                    if choice == "y":
                        from brain.commands.handlers.troubleshoot import _apply_fix
                        if _apply_fix(fix):
                            # Verify the fix was applied by checking the file
                            try:
                                with open(fix["file"], "r") as _f:
                                    content = _f.read()
                                old_text = fix.get("old", "").strip()
                                if fix.get("delete_line") and old_text not in content:
                                    _writeln(f"    {GREEN}✔ Applied & verified{RESET}")
                                elif fix.get("new") and fix["new"].strip() in content:
                                    _writeln(f"    {GREEN}✔ Applied & verified{RESET}")
                                else:
                                    _writeln(f"    {GREEN}✔ Applied{RESET}")
                            except Exception:
                                _writeln(f"    {GREEN}✔ Applied{RESET}")
                            applied += 1
                        else:
                            _writeln(f"    {RED}✘ Failed — line may have already been changed{RESET}")
                    else:
                        _writeln(f"    {DIM}Skipped{RESET}")
                        skipped += 1
                    _writeln()

                brain._pending_fixes = []
                _writeln(f"  {CYAN}Done:{RESET} {applied} applied, {skipped} skipped.")
                if applied > 0:
                    _writeln(f"  {DIM}Run tests with: python -m pytest test/ -q{RESET}")

        except KeyboardInterrupt:
            _writeln()
            spinner.clear()
            _cancelled = True
            try:
                _writeln(f"  {DIM}Ctrl+C again to quit, or keep typing.{RESET}")
                time.sleep(1.5)
            except KeyboardInterrupt:
                session_mgr.save_current()
                await client.close()
                session_mgr.close()
                _exit_alt_screen()
                print("Session saved. JARVIS offline.")
                return
        except EOFError:
            session_mgr.save_current()
            await client.close()
            session_mgr.close()
            _exit_alt_screen()
            print("Session saved. JARVIS offline.")
            return
        except Exception as e:
            _writeln(f"  {RED}✘ {e}{RESET}\n")


def run():
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        pass


if __name__ == "__main__":
    run()
