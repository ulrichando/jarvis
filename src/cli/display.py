"""JARVIS CLI Display — clean, modern tool display.

Design principles (inspired by Claude Code, Aider, Cline):
- Icons for instant tool-type recognition
- Compact one-line tool call header → expanded result below
- Diffs are always shown inline (colored +/-)
- Bash output: exit-code prominence, stderr highlighted, long output collapsed
- File reads: language-aware gutter preview
- Search/grep: path:line:content format, match count header
- Plans: numbered steps with section hierarchy
- Token/cost footer per turn
"""

import difflib
import json
import os
import re
import sys

from src.constants.figures import (
    LIGHTNING_BOLT, PLAY_ICON, PAUSE_ICON,
    BLACK_CIRCLE, BLOCKQUOTE_BAR, HEAVY_HORIZONTAL,
    DIAMOND_OPEN, DIAMOND_FILLED, REFERENCE_MARK, FLAG_ICON,
    EFFORT_LOW, EFFORT_MEDIUM, EFFORT_HIGH, EFFORT_MAX,
    REFRESH_ARROW, FORK_GLYPH,
)

# Import real component implementations
from src.components.Markdown import render_markdown, hasMarkdownSyntax
from src.components.MarkdownTable import render_table as render_markdown_table
from src.components.StructuredDiff import getSyntaxTheme
from src.components.StructuredDiff import (
    renderColorDiff as render_color_diff_raw,
    render_edit_diff,
    render_file_diff,
)
from src.components.Spinner import (
    Spinner, BriefSpinner, SpinnerWithVerb,
    BRAILLE_FRAMES, DOTS_FRAMES,
)
from src.components.Stats import (
    renderStatsToAnsi as render_stats,
    StatsResult, ModelUsage,
)
from src.components.shell.OutputLine import (
    OutputLine as format_output_line,
    tryFormatJson, linkifyUrlsInText,
)
from src.components.FileEditToolDiff import (
    FileEditToolDiff as render_file_edit_diff,
)

# ── ANSI Codes ──
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"
WHITE = "\033[97m"
BG_RED = "\033[41m"
BG_GREEN = "\033[42m"
BG_DARK = "\033[48;5;236m"

# ── True-color Support ────────────────────────────────────────────────────────
# Detected via COLORTERM env var (set by Ghostty, WezTerm, Kitty, iTerm2, etc.)
_TRUECOLOR = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _rgb_fg(r: int, g: int, b: int) -> str:
    """Return a 24-bit foreground ANSI code, or '' if truecolor is unsupported."""
    return f"\033[38;2;{r};{g};{b}m" if _TRUECOLOR else ""


def _rgb_bg(r: int, g: int, b: int) -> str:
    """Return a 24-bit background ANSI code, or '' if truecolor is unsupported."""
    return f"\033[48;2;{r};{g};{b}m" if _TRUECOLOR else ""


# Claude Code–exact color palette (dark-mode defaults)
TC_SUCCESS     = _rgb_fg(78, 186, 101)    # success green
TC_ERROR       = _rgb_fg(255, 107, 128)   # error red
TC_WARN        = _rgb_fg(255, 193, 7)     # warning amber
TC_BRAND       = _rgb_fg(215, 119, 87)    # claude orange
TC_DIFF_ADD_BG = _rgb_bg(34, 92, 43)      # diff added line background
TC_DIFF_DEL_BG = _rgb_bg(122, 41, 54)     # diff removed line background
TC_WORD_ADD_BG = _rgb_bg(56, 166, 96)     # word-level add highlight
TC_WORD_DEL_BG = _rgb_bg(179, 89, 107)    # word-level del highlight

# ── Result Gutter ─────────────────────────────────────────────────────────────
# ⎿ (U+237F) is Claude Code's result-continuation mark; │ for wide body lines
_UTF8_OUT = (sys.stdout.encoding or "").lower() in ("utf-8", "utf8", "utf_8")
RESULT_GUTTER = "\u237f" if _UTF8_OUT else "|"   # ⎿ or |  (first result line)
WIDE_GUTTER   = "\u2502"                          # │        (continuation body)

# ── OSC 8 Terminal Hyperlinks ─────────────────────────────────────────────────
# Supported by Ghostty, WezTerm, Kitty, iTerm2, newer GNOME Terminal (VTE)
_OSC8_TERMS = {"iterm.app", "wezterm", "ghostty"}
_SUPPORT_OSC8 = (
    os.environ.get("TERM_PROGRAM", "").lower() in _OSC8_TERMS
    or "KITTY_WINDOW_ID" in os.environ
    or "VTE_VERSION" in os.environ
)


def _osc8(path: str, display: str) -> str:
    """Wrap *display* text in an OSC 8 terminal hyperlink pointing at *path*."""
    if not _SUPPORT_OSC8 or not path:
        return display
    abs_path = os.path.abspath(path)
    return f"\033]8;;file://{abs_path}\033\\{display}\033]8;;\033\\"

# Tool icons by category -- uses unicode figures from src/constants/figures
TOOL_ICONS = {
    "bash":         LIGHTNING_BOLT,    # ↯  fast execution
    "read_file":    DIAMOND_OPEN,      # ◇  read (hollow)
    "write_file":   DIAMOND_FILLED,    # ◆  write (filled)
    "edit_file":    DIAMOND_FILLED,    # ◆  edit (filled)
    "search_files": REFERENCE_MARK,   # ※  search/grep
    "web_search":   REFRESH_ARROW,    # ↻  web
    "web_fetch":    REFRESH_ARROW,    # ↻  web
    "web_api":      REFRESH_ARROW,    # ↻  web
    "think":        BLACK_CIRCLE,     # ●  thought
    "dispatch":     FORK_GLYPH,       # ⑂  fork/agent
    "tool_search":  REFERENCE_MARK,   # ※  search
    "computer_use": PLAY_ICON,        # ▶  computer
    "view_screen":  PLAY_ICON,        # ▶  screen
    "database":     FLAG_ICON,        # ⚑  db
}

# Language detection from file extension
_EXT_LANG = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html", ".htm": "html",
    ".css": "css",
    ".sql": "sql",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
}

# Effort level display icons (exported for CLI use)
EFFORT_ICONS = {
    "low": EFFORT_LOW,
    "medium": EFFORT_MEDIUM,
    "high": EFFORT_HIGH,
    "max": EFFORT_MAX,
}

# Syntax highlight keyword sets
_KW_PYTHON = {"def", "class", "if", "elif", "else", "for", "while", "return",
               "import", "from", "as", "try", "except", "finally", "with",
               "yield", "raise", "pass", "break", "continue", "lambda",
               "and", "or", "not", "in", "is", "None", "True", "False",
               "self", "async", "await"}
_KW_JS = {"function", "const", "let", "var", "if", "else", "for", "while",
           "return", "import", "export", "from", "class", "new", "this",
           "async", "await", "try", "catch", "finally", "throw",
           "null", "undefined", "true", "false", "switch", "case", "default"}
_KW_RUST = {"fn", "let", "mut", "if", "else", "for", "while", "loop",
             "return", "use", "mod", "pub", "struct", "enum", "impl",
             "trait", "match", "self", "async", "await", "move"}
_KW_BASH = {"if", "then", "else", "elif", "fi", "for", "do", "done",
             "while", "case", "esac", "function", "return", "exit",
             "echo", "export", "local", "readonly", "source", "set"}


def _tw() -> int:
    """Get terminal width."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def format_tokens(n: int) -> str:
    """Format token count: 1234 -> '1.2K', 123456 -> '123K'."""
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}K"
    if n < 1000000:
        return f"{n // 1000}K"
    return f"{n / 1000000:.1f}M"


def _shorten_path(path: str, max_len: int = 50, hyperlink: bool = True) -> str:
    """Shorten a file path for display and optionally wrap in an OSC 8 hyperlink."""
    if not path:
        return ""
    display = path
    try:
        cwd = os.getcwd()
        if display.startswith(cwd):
            display = display[len(cwd):].lstrip("/")
    except Exception:
        pass
    if len(display) > max_len:
        half = (max_len - 3) // 2
        display = display[:half] + "…" + display[-half:]
    return _osc8(path, display) if hyperlink else display


def _lang_from_path(path: str) -> str:
    """Detect language from file extension."""
    if not path:
        return ""
    _, ext = os.path.splitext(path.lower())
    return _EXT_LANG.get(ext, "")


def _highlight_line(line: str, lang: str) -> str:
    """Apply syntax highlighting to a single line."""
    kw_map = {
        "python": _KW_PYTHON, "py": _KW_PYTHON,
        "javascript": _KW_JS, "js": _KW_JS, "typescript": _KW_JS, "ts": _KW_JS,
        "rust": _KW_RUST, "rs": _KW_RUST,
        "bash": _KW_BASH, "sh": _KW_BASH, "zsh": _KW_BASH,
    }
    kws = kw_map.get(lang, set())

    # Comment detection
    comment_char = "#" if lang in ("python", "py", "bash", "sh", "zsh", "shell") else "//"
    if comment_char in line:
        idx = line.index(comment_char)
        before, after = line[:idx], line[idx:]
        if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
            return _highlight_line(before, lang) + f"{GREY}{after}{RESET}"

    # Strings
    line = re.sub(r'(f?"[^"\\]*(?:\\.[^"\\]*)*")', f"{GREEN}\\1{RESET}", line)
    line = re.sub(r"(f?'[^'\\]*(?:\\.[^'\\]*)*')", f"{GREEN}\\1{RESET}", line)
    # Numbers
    line = re.sub(r'\b(\d+\.?\d*)\b', f"{MAGENTA}\\1{RESET}", line)
    # Keywords
    for kw in kws:
        line = re.sub(rf'\b({re.escape(kw)})\b', f"{YELLOW}{BOLD}\\1{RESET}", line)
    # Decorators/attributes
    line = re.sub(r'(@\w+)', f"{CYAN}\\1{RESET}", line)
    return line


def _word_diff_highlight(old_line: str, new_line: str) -> tuple[str, str]:
    """Return (old_highlighted, new_highlighted) with word-level change marks.

    Uses true-color backgrounds when COLORTERM=truecolor; falls back to plain
    text so the output is always readable in 8-color terminals.
    """
    if not _TRUECOLOR:
        # No truecolor — just return syntax-highlighted originals unchanged
        return old_line, new_line

    # Split on word boundaries so operators/punctuation get their own tokens
    tokens_old = re.split(r"(\W+)", old_line)
    tokens_new = re.split(r"(\W+)", new_line)

    sm = difflib.SequenceMatcher(None, tokens_old, tokens_new, autojunk=False)
    old_out: list[str] = []
    new_out: list[str] = []

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        seg_old = "".join(tokens_old[i1:i2])
        seg_new = "".join(tokens_new[j1:j2])
        if op == "equal":
            old_out.append(seg_old)
            new_out.append(seg_new)
        elif op == "replace":
            old_out.append(f"{TC_WORD_DEL_BG}{seg_old}{RESET}")
            new_out.append(f"{TC_WORD_ADD_BG}{seg_new}{RESET}")
        elif op == "delete":
            old_out.append(f"{TC_WORD_DEL_BG}{seg_old}{RESET}")
        elif op == "insert":
            new_out.append(f"{TC_WORD_ADD_BG}{seg_new}{RESET}")

    return "".join(old_out), "".join(new_out)


def _code_block(lines: list[str], lang: str, max_lines: int = 12) -> list[str]:
    """Render lines as a syntax-highlighted code block with fence."""
    tw = min(_tw() - 6, 80)
    label = lang or "text"
    fill = max(0, tw - len(label) - 3)
    out = [f"  {GREY}╭─ {CYAN}{label}{GREY} {'─' * fill}╮{RESET}"]
    shown = lines[:max_lines]
    for ln in shown:
        highlighted = _highlight_line(ln, lang) if lang else ln
        # Pad line to column width so the background covers the full row
        raw_len = len(re.sub(r'\033\[[^m]*m', '', ln))
        padding = " " * max(0, tw - raw_len - 1)
        out.append(f"  {BG_DARK} {highlighted}{padding} {RESET}")
    if len(lines) > max_lines:
        msg = f" … {len(lines) - max_lines} more lines"
        out.append(f"  {GREY}│{DIM}{msg}{RESET}")
    out.append(f"  {GREY}╰{'─' * (tw + 1)}╯{RESET}")
    return out


# ── Tool Call Header ──────────────────────────────────────────────────────────

def tool_call_line(name: str, args: dict) -> str:
    """One-line tool call header: icon + verb + key detail."""
    icon = TOOL_ICONS.get(name, "⚙")

    if name == "bash":
        cmd = args.get("command", "")
        display = cmd[5:] if cmd.startswith("sudo ") else cmd
        # Highlight piped/redirected commands
        pipe_idx = display.find(" | ")
        if pipe_idx > 0 and len(display) > 80:
            display = display[:pipe_idx + 3] + "…"
        elif len(display) > 88:
            display = display[:85] + "…"
        return f"  {icon} {DIM}Bash{RESET}  {CYAN}{display}{RESET}"

    elif name == "read_file":
        path = _shorten_path(args.get("path", ""))
        lang = _lang_from_path(args.get("path", ""))
        lang_tag = f" {DIM}[{lang}]{RESET}" if lang else ""
        offset = args.get("offset")
        limit = args.get("limit")
        range_hint = ""
        if offset or limit:
            s = offset or 1
            e = (offset or 1) + (limit or 200)
            range_hint = f" {DIM}:{s}–{e}{RESET}"
        return f"  {icon} {DIM}Read{RESET}  {path}{lang_tag}{range_hint}"

    elif name == "write_file":
        path = _shorten_path(args.get("path", ""))
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        lang = _lang_from_path(args.get("path", ""))
        lang_tag = f" {DIM}[{lang}]{RESET}" if lang else ""
        return f"  {icon} {DIM}Write{RESET}  {path}{lang_tag}  {DIM}({lines} lines){RESET}"

    elif name == "edit_file":
        path = _shorten_path(args.get("path", ""))
        old = args.get("old_string", "")
        old_preview = old.split("\n")[0][:50].strip() if old else ""
        preview = f"  {DIM}'{old_preview}'…{RESET}" if old_preview else ""
        return f"  {icon} {DIM}Edit{RESET}  {path}{preview}"

    elif name == "search_files":
        pattern = args.get("pattern", "")
        path = args.get("path", ".")
        mode = args.get("mode", "glob")
        if mode == "grep":
            return (f"  {icon} {DIM}Grep{RESET}  "
                    f"{CYAN}{pattern}{RESET}  {DIM}in {_shorten_path(path)}{RESET}")
        return (f"  {icon} {DIM}Glob{RESET}  "
                f"{CYAN}{pattern}{RESET}  {DIM}in {_shorten_path(path)}{RESET}")

    elif name == "web_search":
        query = args.get("query", "")
        return f"  {icon} {DIM}Web search{RESET}  {query[:70]}"

    elif name == "web_fetch":
        url = args.get("url", "")
        return f"  {icon} {DIM}Fetch{RESET}  {url[:70]}"

    elif name == "web_api":
        method = args.get("method", "GET")
        url = args.get("url", "")
        platform = args.get("platform", "")
        label = f"{platform}: " if platform else ""
        return f"  {icon} {DIM}{method}{RESET}  {label}{url[:60]}"

    elif name == "think":
        thought = args.get("thought", args.get("content", ""))
        preview = thought[:60].replace("\n", " ") if thought else "…"
        return f"  {icon} {DIM}Think{RESET}  {DIM}{preview}{RESET}"

    elif name == "dispatch":
        agent = args.get("agent_type", "scout")
        task = args.get("task", "")[:60]
        return f"  {icon} {DIM}Spawn {CYAN}{agent}{RESET}{DIM}{RESET}  {task}"

    elif name == "tool_search":
        query = args.get("query", "")
        return f"  {icon} {DIM}Tool search{RESET}  {query}"

    elif name == "computer_use":
        action = args.get("action", "?")
        return f"  {icon} {DIM}Computer{RESET}  {action}"

    elif name == "database":
        query = args.get("query", "")[:70]
        return f"  {icon} {DIM}SQL{RESET}  {query}"

    elif name.startswith("mcp_"):
        clean = name.replace("mcp_", "").replace("__", "/")
        first_arg = next(iter(args.values()), "") if args else ""
        preview = str(first_arg)[:50] if first_arg else ""
        return f"  ⚙ {DIM}MCP/{clean}{RESET}  {preview}"

    else:
        preview = str(args)[:60] if args else ""
        return f"  ⚙ {DIM}{name}{RESET}  {preview}"


# ── Tool Result Body ──────────────────────────────────────────────────────────

def tool_result_line(name: str, result: str, success: bool, elapsed: float) -> str:
    """Tool result: ⎿ continuation gutter + status + elapsed + compact output."""
    tc_ok  = (TC_SUCCESS or GREEN)
    tc_err = (TC_ERROR   or RED)

    # Empty result — just status
    if not result or not result.strip():
        icon = f"{tc_ok}✔{RESET}" if success else f"{tc_err}✘{RESET}"
        return f"  {RESULT_GUTTER} {icon}  {DIM}{elapsed:.1f}s{RESET}"

    status_icon = f"{tc_ok}✔{RESET}" if success else f"{tc_err}✘{RESET}"
    time_str    = f"{DIM}{elapsed:.1f}s{RESET}"
    out = []

    if name == "bash":
        bash_lines = _format_bash_result(result, success, elapsed)
        # Prefix first bash line with ⎿ to tie it visually to the tool call
        if bash_lines:
            bash_lines[0] = f"  {RESULT_GUTTER}" + bash_lines[0].lstrip()
        out.extend(bash_lines)

    elif name in ("search_files",):
        lines = [l for l in result.strip().split("\n") if l.strip()]
        count_str = f"{len(lines)} match{'es' if len(lines) != 1 else ''}"
        out.append(f"  {status_icon}  {DIM}{count_str}{RESET}  {time_str}")
        for line in lines[:8]:
            # grep-style: file:lineno:content
            if re.match(r'^[^:]+:\d+:', line):
                parts = line.split(":", 2)
                fpath = _shorten_path(parts[0])
                lineno = parts[1]
                content = parts[2] if len(parts) > 2 else ""
                out.append(
                    f"  {DIM}{WIDE_GUTTER}{RESET}  "
                    f"{CYAN}{fpath}{RESET}{DIM}:{lineno}{RESET}  {content[:80]}"
                )
            else:
                out.append(f"  {DIM}{WIDE_GUTTER}{RESET}  {line[:100]}")
        if len(lines) > 8:
            out.append(f"  {DIM}{WIDE_GUTTER}  … {len(lines) - 8} more{RESET}")

    elif name == "web_search":
        lines = result.strip().split("\n")
        out.append(f"  {status_icon}  {DIM}{len(lines)} results{RESET}  {time_str}")
        for line in lines[:6]:
            out.append(f"  {DIM}{WIDE_GUTTER}{RESET}  {line[:100]}")
        if len(lines) > 6:
            out.append(f"  {DIM}{WIDE_GUTTER}  … {len(lines) - 6} more{RESET}")

    elif name == "read_file":
        lines = result.strip().split("\n")
        out.append(f"  {status_icon}  {DIM}{len(lines)} lines{RESET}  {time_str}")
        # Caller shows diff for edits; don't repeat raw content here

    elif name == "write_file":
        out.append(f"  {status_icon}  {DIM}written{RESET}  {time_str}")

    elif name == "edit_file":
        out.append(f"  {status_icon}  {DIM}edited{RESET}  {time_str}")

    elif name == "think":
        lines = result.strip().split("\n")
        first = next((l for l in lines if l.strip()), "")
        out.append(f"  {status_icon}  {DIM}{first[:80]}{RESET}  {time_str}")
        for line in lines[1:3]:
            if line.strip():
                out.append(f"  {DIM}{WIDE_GUTTER}  {line[:100]}{RESET}")

    elif name == "dispatch":
        lines = result.strip().split("\n")
        out.append(f"  {status_icon}  {DIM}agent done{RESET}  {time_str}")
        for line in lines[:4]:
            if line.strip():
                out.append(f"  {DIM}{WIDE_GUTTER}  {line[:100]}{RESET}")
        if len(lines) > 4:
            out.append(f"  {DIM}{WIDE_GUTTER}  … {len(lines) - 4} more{RESET}")

    else:
        lines = result.strip().split("\n")
        out.append(f"  {status_icon}  {time_str}")
        for line in lines[:5]:
            out.append(f"  {DIM}{WIDE_GUTTER}{RESET}  {line[:120]}")
        if len(lines) > 5:
            out.append(f"  {DIM}{WIDE_GUTTER}  … {len(lines) - 5} more{RESET}")

    return "\n".join(out)


def _json_highlight(text: str) -> str:
    """Apply simple syntax highlighting to a JSON string (keys, strings, numbers, booleans)."""
    # Keys: "key":
    text = re.sub(r'"([^"]+)"(\s*:)', f"{CYAN}\"\\1\"{RESET}\\2", text)
    # String values: ": "value"
    text = re.sub(r'(:\s*)"([^"]*)"', f"\\1{GREEN}\"\\2\"{RESET}", text)
    # Numbers
    text = re.sub(r'(:\s*)(-?\d+\.?\d*(?:[eE][+-]?\d+)?)', f"\\1{MAGENTA}\\2{RESET}", text)
    # Booleans / null
    text = re.sub(r'\b(true|false|null)\b', f"{YELLOW}\\1{RESET}", text)
    return text


def _format_bash_result(result: str, success: bool, elapsed: float) -> list[str]:
    """Format bash output: prominent exit-code header, framed stderr, JSON auto-highlight."""
    out = []
    lines = result.strip().split("\n")

    # ── Parse structured sections (exit_code= / ---STDERR--- markers) ──
    exit_code = None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    current_section = "stdout"

    for line in lines:
        if line.startswith("exit_code="):
            try:
                exit_code = int(line.split("=", 1)[1].split()[0])
            except (ValueError, IndexError):
                exit_code = 0
            current_section = "stdout"
        elif line.startswith("STDERR:") or line.startswith("stderr:"):
            current_section = "stderr"
        elif line in ("---STDERR---",):
            current_section = "stderr"
        elif line in ("---STDOUT---",):
            current_section = "stdout"
        else:
            (stderr_lines if current_section == "stderr" else stdout_lines).append(line)

    # ── Status header: ✔ 0.4s  or  ✘ exit 1  0.4s ──
    if not success or (exit_code is not None and exit_code != 0):
        code_label = f"exit {exit_code}" if exit_code is not None else "error"
        tc_err = TC_ERROR or RED
        out.append(
            f"  {tc_err}{BOLD}✘ {code_label}{RESET}  {DIM}{elapsed:.1f}s{RESET}"
        )
    else:
        tc_ok = TC_SUCCESS or GREEN
        out.append(f"  {tc_ok}✔{RESET}  {DIM}{elapsed:.1f}s{RESET}")

    # ── Stderr — framed amber block ──────────────────────────────────────────
    if stderr_lines:
        tc_warn = TC_WARN or YELLOW
        tw = min(_tw() - 6, 72)
        out.append(f"  {tc_warn}┌─ stderr {'─' * max(0, tw - 9)}┐{RESET}")
        for ln in stderr_lines[:6]:
            padded = ln[:tw].ljust(tw)
            out.append(f"  {tc_warn}│{RESET}  {YELLOW}{padded}{RESET}")
        if len(stderr_lines) > 6:
            out.append(
                f"  {tc_warn}│{RESET}  {DIM}… {len(stderr_lines) - 6} more lines{RESET}"
            )
        out.append(f"  {tc_warn}└{'─' * (tw + 2)}┘{RESET}")

    # ── Stdout ────────────────────────────────────────────────────────────────
    MAX_STDOUT = 10
    if stdout_lines:
        joined = "\n".join(stdout_lines)
        stripped = joined.lstrip()

        # JSON: pretty-print + syntax highlight (with precision-loss guard)
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(joined)
                pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                # Precision-loss guard: skip if round-trip changed the data
                if json.loads(pretty) == parsed:
                    stdout_lines = pretty.split("\n")
                    # Apply JSON syntax highlighting
                    stdout_lines = [_json_highlight(ln) for ln in stdout_lines]
            except Exception:
                pass

        for ln in stdout_lines[:MAX_STDOUT]:
            out.append(f"  {DIM}{WIDE_GUTTER}{RESET}  {ln[:120]}")
        if len(stdout_lines) > MAX_STDOUT:
            out.append(
                f"  {DIM}{WIDE_GUTTER}  … {len(stdout_lines) - MAX_STDOUT} more lines{RESET}"
            )

    return out


# ── Tool Result Preview (compact) ─────────────────────────────────────────────

def tool_result_preview(result: str, max_lines: int = 8) -> str:
    """Show first N lines of tool output with ⎿/│ gutter."""
    if not result.strip():
        return ""
    lines = result.strip().split("\n")
    out = []
    for i, line in enumerate(lines[:max_lines]):
        gutter = RESULT_GUTTER if i == 0 else WIDE_GUTTER
        out.append(f"  {DIM}{gutter}{RESET}  {line[:120]}")
    if len(lines) > max_lines:
        out.append(f"  {DIM}{WIDE_GUTTER}  … {len(lines) - max_lines} more lines{RESET}")
    return "\n".join(out)


# ── Diff Display ──────────────────────────────────────────────────────────────

def diff_display(old_string: str, new_string: str, path: str) -> str:
    """Render a unified diff with truecolor backgrounds and word-level highlights.

    Pairs consecutive removal/addition lines to show word-level diffs on single
    changed lines. Truecolor backgrounds are used when COLORTERM=truecolor.
    Falls back gracefully to standard 8-color ANSI in dumb terminals.
    """
    old_lines = old_string.splitlines()
    new_lines = new_string.splitlines()

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    if not diff:
        return ""

    lang = _lang_from_path(path)
    short = _shorten_path(path)
    out = [f"  {BOLD}{short}{RESET}  {DIM}{lang}{RESET}" if lang else f"  {BOLD}{short}{RESET}"]

    # Buffer consecutive - and + lines so we can pair them for word-level diffs
    rem_buf: list[str] = []
    add_buf: list[str] = []

    def _flush_hunk() -> None:
        pairs = min(len(rem_buf), len(add_buf))
        for i in range(pairs):
            old_hl, new_hl = _word_diff_highlight(rem_buf[i], add_buf[i])
            # Use base lang highlight on whatever the word diff didn't paint
            old_base = _highlight_line(rem_buf[i], lang) if lang else rem_buf[i]
            new_base = _highlight_line(add_buf[i], lang) if lang else add_buf[i]
            # Prefer word-diff output when truecolor is on, else use base highlight
            old_rendered = old_hl if _TRUECOLOR else old_base
            new_rendered = new_hl if _TRUECOLOR else new_base
            del_bg = TC_DIFF_DEL_BG if _TRUECOLOR else ""
            add_bg = TC_DIFF_ADD_BG if _TRUECOLOR else ""
            out.append(f"  {del_bg}{RED}-{RESET} {old_rendered}")
            out.append(f"  {add_bg}{GREEN}+{RESET} {new_rendered}")
        # Unmatched removals (more dels than adds)
        for line in rem_buf[pairs:]:
            hl = _highlight_line(line, lang) if lang else line
            del_bg = TC_DIFF_DEL_BG if _TRUECOLOR else ""
            out.append(f"  {del_bg}{RED}-{RESET} {DIM}{hl}{RESET}")
        # Unmatched additions (more adds than dels)
        for line in add_buf[pairs:]:
            hl = _highlight_line(line, lang) if lang else line
            add_bg = TC_DIFF_ADD_BG if _TRUECOLOR else ""
            out.append(f"  {add_bg}{GREEN}+{RESET} {hl}")
        rem_buf.clear()
        add_buf.clear()

    for raw in diff[2:]:  # skip --- and +++ header lines
        if raw.startswith("-"):
            add_buf and _flush_hunk()  # flush if we had isolated adds before
            rem_buf.append(raw[1:])
        elif raw.startswith("+"):
            add_buf.append(raw[1:])
        elif raw.startswith("@@"):
            _flush_hunk()
            out.append(f"  {CYAN}{DIM}{raw}{RESET}")
        else:
            _flush_hunk()
            content = raw.lstrip(" ")
            hl = _highlight_line(content, lang) if lang else content
            out.append(f"    {DIM}{hl}{RESET}")

    _flush_hunk()
    return "\n".join(out)


# ── Plan Display ──────────────────────────────────────────────────────────────

def plan_display(text: str) -> str:
    """Render a structured plan with visual hierarchy.

    Handles: ## section headers, numbered steps, bullet sub-items, code fences,
    inline bold/code/italic/links. Uses truecolor brand accent for step numbers.
    """
    if not text.strip():
        return ""

    lines = text.strip().split("\n")
    out: list[str] = []
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    step_count = 0

    for line in lines:
        stripped = line.strip()

        # ── Code fence ──────────────────────────────────────────────────────
        if stripped.startswith("```"):
            if not in_code:
                code_lang = stripped[3:].strip()
                in_code = True
                code_buf = []
            else:
                out.extend(_code_block(code_buf, code_lang, max_lines=20))
                in_code = False
                code_lang = ""
                code_buf = []
            continue
        if in_code:
            code_buf.append(line)
            continue

        # ── Section headers ────────────────────────────────────────────────
        if stripped.startswith("#### "):
            out.append(f"\n    {GREY}{stripped[5:]}{RESET}")
        elif stripped.startswith("### "):
            out.append(f"\n  {CYAN}{stripped[4:]}{RESET}")
        elif stripped.startswith("## "):
            tw = _tw()
            title = stripped[3:]
            out.append("")
            out.append(f"  {WHITE}{BOLD}{title}{RESET}")
            out.append(f"  {DIM}{'─' * min(len(title) + 2, tw - 4)}{RESET}")
        elif stripped.startswith("# "):
            tc_brand = TC_BRAND or YELLOW
            out.append(f"\n  {tc_brand}{BOLD}{stripped[2:]}{RESET}")

        # ── Numbered steps ─────────────────────────────────────────────────
        elif re.match(r'^\d+\.\s', stripped):
            m = re.match(r'^(\d+)\.\s+(.*)', stripped)
            if m:
                step_count += 1
                num = m.group(1)
                content = _plan_inline(m.group(2))
                tc_brand = TC_BRAND or CYAN
                out.append(f"\n  {tc_brand}{BOLD}{num}.{RESET}  {content}")

        # ── Checkboxes ─────────────────────────────────────────────────────
        elif re.match(r'^\[[ xX]\]\s', stripped):
            checked = stripped[1] in "xX"
            rest = _plan_inline(stripped[4:])
            mark = f"{GREEN}✔{RESET}" if checked else f"{DIM}○{RESET}"
            out.append(f"     {mark}  {rest}")

        # ── Bullets ────────────────────────────────────────────────────────
        elif re.match(r'^[-*]\s', stripped):
            indent = len(line) - len(line.lstrip())
            content = _plan_inline(stripped[2:])
            prefix = "  " * (indent // 2 + 1)
            out.append(f"{prefix}  {DIM}•{RESET}  {content}")

        # ── Horizontal rule ────────────────────────────────────────────────
        elif stripped in ("---", "***", "___"):
            out.append(f"  {DIM}{'─' * min(_tw() - 4, 60)}{RESET}")

        # ── Blockquote ─────────────────────────────────────────────────────
        elif stripped.startswith("> "):
            out.append(f"  {CYAN}{BLOCKQUOTE_BAR}{RESET}  {DIM}{_plan_inline(stripped[2:])}{RESET}")

        # ── Normal text ────────────────────────────────────────────────────
        elif stripped:
            out.append(f"  {_plan_inline(stripped)}")
        else:
            out.append("")

    return "\n".join(out)


def _plan_inline(text: str) -> str:
    """Inline formatting for plan text: bold, code, italic, links."""
    text = re.sub(r'`([^`]+)`', f"{GREEN}\\1{RESET}", text)
    text = re.sub(r'\*\*([^*]+)\*\*', f"{YELLOW}{BOLD}\\1{RESET}", text)
    text = re.sub(r'\*([^*]+)\*', f"{MAGENTA}\\1{RESET}", text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', f"{BLUE}{UNDERLINE}\\1{RESET}", text)
    return text


# ── Permission Prompt ──────────────────────────────────────────────────────────

def permission_prompt(tool_name: str, args: dict) -> str:
    """Permission prompt with action description and inline preview."""
    if tool_name == "write_file":
        path = _shorten_path(args.get("path", "?"))
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        desc = f"Write {lines} lines to {CYAN}{path}{RESET}"
    elif tool_name == "edit_file":
        path = _shorten_path(args.get("path", "?"))
        desc = f"Edit {CYAN}{path}{RESET}"
    elif tool_name == "bash":
        cmd = args.get("command", "?")
        if len(cmd) > 88:
            cmd = cmd[:85] + "…"
        desc = f"Run:  {CYAN}{cmd}{RESET}"
    elif tool_name == "dispatch":
        agent = args.get("agent_type", "?")
        task = args.get("task", "")[:60]
        desc = f"Spawn {CYAN}{agent}{RESET}: {task}"
    else:
        desc = f"{tool_name}: {str(args)[:60]}"

    out = [f"  {YELLOW}⚠  {desc}"]

    # Diff preview for edits
    if tool_name == "edit_file" and args.get("old_string") and args.get("new_string"):
        path = args.get("path", "")
        lang = _lang_from_path(path)
        for line in args["old_string"].splitlines()[:4]:
            hl = _highlight_line(line, lang) if lang else line
            out.append(f"  {RED}-  {hl}{RESET}")
        for line in args["new_string"].splitlines()[:4]:
            hl = _highlight_line(line, lang) if lang else line
            out.append(f"  {GREEN}+  {hl}{RESET}")

    # Content preview for writes
    if tool_name == "write_file" and args.get("content"):
        path = args.get("path", "")
        lang = _lang_from_path(path)
        preview_lines = args["content"].split("\n")[:4]
        for line in preview_lines:
            hl = _highlight_line(line, lang) if lang else line
            out.append(f"  {DIM}   {hl}{RESET}")
        total = args["content"].count("\n") + 1
        if total > 4:
            out.append(f"  {DIM}   … {total - 4} more lines{RESET}")

    # Bash command — show dangerous patterns
    if tool_name == "bash":
        cmd = args.get("command", "")
        danger_patterns = ["rm -rf", "rm -f", "sudo rm", "> /", "dd if=",
                           "mkfs", "chmod 777", "curl.*| *bash", "wget.*| *sh"]
        for pat in danger_patterns:
            if re.search(pat, cmd):
                out.append(f"  {RED}⚠ Potentially destructive command{RESET}")
                break

    out.append("")
    out.append(f"  {BOLD}Allow?{RESET}  "
               f"{GREEN}[y]{RESET} yes  "
               f"{RED}[n]{RESET} no  "
               f"{CYAN}[a]{RESET} always  "
               f"{YELLOW}[N]{RESET} never")
    return "\n".join(out)


# ── Status Bar ────────────────────────────────────────────────────────────────

def status_bar_text(model: str, session: str, tokens_used: int,
                    mode: str, cost: float = 0.0) -> str:
    """Status line with model, tokens, mode, and cost."""
    parts = []
    if model:
        parts.append(model)
    if tokens_used > 0:
        parts.append(f"{format_tokens(tokens_used)} tokens")
    if mode and mode != "normal":
        parts.append(mode)
    if cost > 0.001:
        parts.append(f"${cost:.2f}")
    return f"{DIM}{' · '.join(parts)}{RESET}"


# ── Token Footer ──────────────────────────────────────────────────────────────

def token_footer(tokens: int, tool_count: int, elapsed: float,
                 cost: float = 0.0) -> str:
    """Post-response footer with usage stats and cost."""
    parts = []
    if tokens > 0:
        parts.append(format_tokens(tokens) + " tokens")
    if tool_count > 0:
        parts.append(f"{tool_count} tool{'s' if tool_count != 1 else ''}")
    parts.append(f"{elapsed:.1f}s")
    if cost > 0.001:
        parts.append(f"${cost:.2f}")
    return f"  {DIM}{' · '.join(parts)}{RESET}"


# ── Collapsed Tool Group ──────────────────────────────────────────────────────

def collapsed_tool_group(tool_calls: list[dict], verbose: bool = False) -> str:
    """Render a group of consecutive read/search tools as a collapsed summary.

    When verbose=False: one-line summary like '◇ Read 3 files, ※ searched 2 patterns'
    When verbose=True: each tool call individually.
    """
    if not tool_calls:
        return ""
    if verbose or len(tool_calls) <= 1:
        return "\n".join(tool_call_line(tc["name"], tc.get("args", {})) for tc in tool_calls)

    reads = [tc for tc in tool_calls if tc["name"] == "read_file"]
    searches = [tc for tc in tool_calls
                if tc["name"] in ("search_files", "web_search")]
    others = [tc for tc in tool_calls if tc not in reads and tc not in searches]

    parts = []
    if reads:
        paths = [_shorten_path(tc.get("args", {}).get("path", "")) for tc in reads]
        if len(paths) <= 2:
            parts.append(f"{DIAMOND_OPEN} Read {', '.join(paths)}")
        else:
            parts.append(f"{DIAMOND_OPEN} Read {len(reads)} files")
    if searches:
        patterns = [tc.get("args", {}).get("pattern",
                    tc.get("args", {}).get("query", "")) for tc in searches]
        if len(patterns) <= 2:
            parts.append(f"{REFERENCE_MARK} searched '{', '.join(p[:30] for p in patterns)}'")
        else:
            parts.append(f"{REFERENCE_MARK} {len(searches)} searches")
    if others:
        parts.append(f"⚙ {len(others)} other tools")

    summary = "  ".join(parts)
    return f"  {DIM}{summary}  ({len(tool_calls)} calls){RESET}"


# ── Component Wrappers ────────────────────────────────────────────────────────

def render_md(text: str, width: int = 0) -> str:
    """Render markdown to ANSI terminal output using the Markdown component."""
    return render_markdown(text, width=width)


def render_diff(old_string: str, new_string: str, path: str = "",
                context_lines: int = 3) -> str:
    """Render an edit diff using the StructuredDiff component."""
    return render_edit_diff(old_string, new_string, path, context_lines)


def render_bash_output(text: str, exit_code: int = None,
                       elapsed: float = None, max_lines: int = 50) -> str:
    """Format bash output using the OutputLine component."""
    return format_output_line(text, exit_code=exit_code, elapsed=elapsed,
                              max_lines=max_lines)


def render_file_edit(file_path: str, old_string: str, new_string: str,
                     file_content: str = "", framed: bool = False) -> str:
    """Render a file edit diff using the FileEditToolDiff component."""
    return render_file_edit_diff(
        file_path=file_path, old_string=old_string,
        new_string=new_string, file_content=file_content,
        framed=framed,
    )


def render_usage_stats(stats: StatsResult, tab: str = "overview") -> str:
    """Render usage statistics using the Stats component."""
    return render_stats(stats, tab=tab)


def create_spinner(text: str = "", style: str = "braille") -> Spinner:
    """Create a terminal spinner.

    Args:
        text: Action text to display.
        style: 'braille' or 'dots'.

    Returns:
        Spinner instance (call .start() to begin animation).
    """
    if style == "dots":
        return BriefSpinner(text)
    return Spinner(text)
