"""Markdown to ANSI terminal renderer.

Converts markdown text to ANSI-colored terminal output with support for:
- Headers (bold + color)
- Code blocks (syntax highlighting via pygments)
- Inline code (dim background)
- Bold/italic
- Links (underline)
- Lists (bullets with indentation)
- Blockquotes (dim with bar prefix)
- Tables (delegated to MarkdownTable)
"""

from __future__ import annotations

import re
from typing import Optional

# ANSI codes
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
WHITE = "\033[97m"
GREY = "\033[90m"
BG_GREY = "\033[48;5;236m"

# Header colors by level
HEADER_COLORS = {
    1: f"{BOLD}{CYAN}",
    2: f"{BOLD}{BLUE}",
    3: f"{BOLD}{MAGENTA}",
    4: f"{BOLD}{YELLOW}",
    5: f"{BOLD}{GREEN}",
    6: f"{BOLD}{WHITE}",
}

TOKEN_CACHE_MAX = 1024


def hasMarkdownSyntax(text: str) -> bool:
    """Check if text contains markdown syntax."""
    patterns = [
        r'^#{1,6}\s',      # headers
        r'\*\*\w',          # bold
        r'\*\w',            # italic
        r'`[^`]+`',         # inline code
        r'```',             # code blocks
        r'^\s*[-*+]\s',     # lists
        r'^\s*\d+\.\s',    # ordered lists
        r'^\s*>',           # blockquotes
        r'\[.+\]\(.+\)',    # links
        r'^\|.+\|',         # tables
        r'^---+$',          # horizontal rules
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.MULTILINE):
            return True
    return False


def _highlight_code(code: str, language: str = "") -> str:
    """Syntax highlight code using pygments if available."""
    try:
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
        from pygments.formatters import TerminalTrueColorFormatter

        try:
            if language:
                lexer = get_lexer_by_name(language, stripall=True)
            else:
                lexer = guess_lexer(code)
        except Exception:
            lexer = TextLexer()

        formatter = TerminalTrueColorFormatter(style="monokai")
        return highlight(code, lexer, formatter).rstrip()
    except ImportError:
        # No pygments -- return with basic dim styling
        return f"{DIM}{code}{RESET}"


def _render_inline(text: str) -> str:
    """Render inline markdown: bold, italic, code, links."""
    # Inline code (must be before bold/italic to avoid conflicts)
    text = re.sub(
        r'`([^`]+)`',
        lambda m: f"{BG_GREY}{DIM} {m.group(1)} {RESET}",
        text,
    )

    # Bold + italic
    text = re.sub(
        r'\*\*\*(.+?)\*\*\*',
        lambda m: f"{BOLD}{ITALIC}{m.group(1)}{RESET}",
        text,
    )

    # Bold
    text = re.sub(
        r'\*\*(.+?)\*\*',
        lambda m: f"{BOLD}{m.group(1)}{RESET}",
        text,
    )
    text = re.sub(
        r'__(.+?)__',
        lambda m: f"{BOLD}{m.group(1)}{RESET}",
        text,
    )

    # Italic
    text = re.sub(
        r'\*(.+?)\*',
        lambda m: f"{ITALIC}{m.group(1)}{RESET}",
        text,
    )
    text = re.sub(
        r'_(.+?)_',
        lambda m: f"{ITALIC}{m.group(1)}{RESET}",
        text,
    )

    # Links: [text](url)
    text = re.sub(
        r'\[(.+?)\]\((.+?)\)',
        lambda m: f"{UNDERLINE}{BLUE}{m.group(1)}{RESET} {DIM}({m.group(2)}){RESET}",
        text,
    )

    # Strikethrough
    text = re.sub(
        r'~~(.+?)~~',
        lambda m: f"\033[9m{m.group(1)}{RESET}",
        text,
    )

    return text


def render_markdown(text: str, width: int = 0) -> str:
    """Render markdown text to ANSI terminal output.

    Args:
        text: Markdown source text.
        width: Terminal width (0 = auto-detect).

    Returns:
        ANSI-formatted string for terminal display.
    """
    if not text:
        return ""

    if width <= 0:
        import os
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80

    lines = text.split("\n")
    output: list[str] = []
    in_code_block = False
    code_block_lines: list[str] = []
    code_language = ""
    in_table = False
    table_lines: list[str] = []

    def _flush_table():
        nonlocal in_table, table_lines
        if table_lines:
            from src.components.MarkdownTable import render_table
            output.append(render_table(table_lines, width))
            table_lines = []
        in_table = False

    for line in lines:
        # Code block toggle
        if line.strip().startswith("```"):
            if in_code_block:
                # End code block
                code = "\n".join(code_block_lines)
                highlighted = _highlight_code(code, code_language)
                # Box the code
                output.append(f"{DIM}\u2500\u2500\u2500 {code_language or 'code'} \u2500\u2500\u2500{RESET}")
                for cline in highlighted.split("\n"):
                    output.append(f"  {cline}")
                output.append(f"{DIM}\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500{RESET}")
                code_block_lines = []
                code_language = ""
                in_code_block = False
            else:
                # Flush table if we were in one
                if in_table:
                    _flush_table()
                # Start code block
                lang_match = re.match(r'```(\w+)', line.strip())
                code_language = lang_match.group(1) if lang_match else ""
                in_code_block = True
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        stripped = line.strip()

        # Table detection
        if re.match(r'^\|.+\|$', stripped):
            if not in_table:
                in_table = True
            table_lines.append(stripped)
            continue
        elif in_table:
            _flush_table()

        # Horizontal rule
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            hr = "\u2500" * min(width - 4, 60)
            output.append(f"  {DIM}{hr}{RESET}")
            continue

        # Headers
        header_match = re.match(r'^(#{1,6})\s+(.*)', line)
        if header_match:
            level = len(header_match.group(1))
            text_content = header_match.group(2)
            color = HEADER_COLORS.get(level, BOLD)
            rendered = _render_inline(text_content)
            if level == 1:
                output.append("")
                output.append(f"{color}{rendered}{RESET}")
                output.append(f"{color}{'=' * len(text_content)}{RESET}")
            elif level == 2:
                output.append("")
                output.append(f"{color}{rendered}{RESET}")
                output.append(f"{color}{'-' * len(text_content)}{RESET}")
            else:
                output.append(f"{color}{'#' * level} {rendered}{RESET}")
            continue

        # Blockquote
        if stripped.startswith(">"):
            quote_text = re.sub(r'^>\s*', '', stripped)
            rendered = _render_inline(quote_text)
            output.append(f"  {DIM}\u258e {rendered}{RESET}")
            continue

        # Unordered list
        list_match = re.match(r'^(\s*)([-*+])\s+(.*)', line)
        if list_match:
            indent_str = list_match.group(1)
            depth = len(indent_str) // 2
            content = list_match.group(3)
            bullets = ["\u2022", "\u25e6", "\u2023", "\u2043"]
            bullet = bullets[depth % len(bullets)]
            indent = "  " * (depth + 1)
            rendered = _render_inline(content)
            output.append(f"{indent}{bullet} {rendered}")
            continue

        # Ordered list
        ol_match = re.match(r'^(\s*)(\d+)\.\s+(.*)', line)
        if ol_match:
            indent_str = ol_match.group(1)
            number = ol_match.group(2)
            content = ol_match.group(3)
            depth = len(indent_str) // 2
            indent = "  " * (depth + 1)
            rendered = _render_inline(content)
            output.append(f"{indent}{DIM}{number}.{RESET} {rendered}")
            continue

        # Regular paragraph
        if stripped:
            rendered = _render_inline(line)
            output.append(rendered)
        else:
            output.append("")

    # Flush any remaining table
    if in_table:
        _flush_table()

    return "\n".join(output)


# Aliases for the stub API
def Markdown(text: str, **kwargs) -> str:
    """Render markdown to ANSI. Primary entry point."""
    return render_markdown(text, **kwargs)


def MarkdownWithHighlight(text: str, highlight_term: str = "", **kwargs) -> str:
    """Render markdown with search term highlighting."""
    rendered = render_markdown(text, **kwargs)
    if highlight_term:
        # Highlight the search term with yellow background
        pattern = re.compile(re.escape(highlight_term), re.IGNORECASE)
        rendered = pattern.sub(
            lambda m: f"\033[43m\033[30m{m.group()}{RESET}",
            rendered,
        )
    return rendered


def MarkdownBody(text: str, **kwargs) -> str:
    """Render markdown body (same as Markdown for terminal)."""
    return render_markdown(text, **kwargs)


def flushNonTableContent(lines: list[str]) -> str:
    """Flush accumulated non-table lines as rendered markdown."""
    return render_markdown("\n".join(lines))


def StreamingMarkdown(text: str, **kwargs) -> str:
    """Render streaming markdown (same as Markdown for terminal)."""
    return render_markdown(text, **kwargs)


def cachedLexer(language: str):
    """Return a cached pygments lexer for the given language."""
    try:
        from pygments.lexers import get_lexer_by_name
        return get_lexer_by_name(language, stripall=True)
    except Exception:
        return None


class Props:
    """Properties for markdown rendering."""
    def __init__(self, text: str = "", width: int = 0):
        self.text = text
        self.width = width


class StreamingProps(Props):
    """Properties for streaming markdown rendering."""
    pass
