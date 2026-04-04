"""Syntax-aware color diff utilities.

Provides syntax highlighting for diff content using pygments when available.
"""

from __future__ import annotations

from typing import Optional

# ANSI codes
RESET = "\033[0m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"


def getColorModuleUnavailableReason() -> Optional[str]:
    """Check if pygments is available for syntax coloring.

    Returns None if available, or a reason string if not.
    """
    try:
        import pygments
        return None
    except ImportError:
        return "pygments is not installed (pip install pygments)"


def getSyntaxTheme() -> str:
    """Get the current syntax highlighting theme name."""
    try:
        import pygments
        return "monokai"
    except ImportError:
        return "none"


def expectColorDiff(diff_text: str, language: str = "") -> str:
    """Apply syntax coloring to diff content.

    Colors additions green, deletions red, and applies syntax highlighting
    to the content within each line when pygments is available.

    Args:
        diff_text: Unified diff text.
        language: Programming language for highlighting.

    Returns:
        ANSI-colored diff text.
    """
    if not diff_text:
        return ""

    try:
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name, TextLexer
        from pygments.formatters import TerminalTrueColorFormatter

        try:
            lexer = get_lexer_by_name(language, stripall=True) if language else TextLexer()
        except Exception:
            lexer = TextLexer()

        formatter = TerminalTrueColorFormatter(style="monokai")

        output: list[str] = []
        for line in diff_text.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                # Highlight content after the +
                content = line[1:]
                colored = highlight(content, lexer, formatter).rstrip()
                output.append(f"{GREEN}+{colored}{RESET}")
            elif line.startswith("-") and not line.startswith("---"):
                content = line[1:]
                colored = highlight(content, lexer, formatter).rstrip()
                output.append(f"{RED}-{colored}{RESET}")
            elif line.startswith("@@"):
                output.append(f"{CYAN}{line}{RESET}")
            else:
                output.append(line)
        return "\n".join(output)

    except ImportError:
        # No pygments -- fall back to basic coloring
        output = []
        for line in diff_text.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                output.append(f"{GREEN}{line}{RESET}")
            elif line.startswith("-") and not line.startswith("---"):
                output.append(f"{RED}{line}{RESET}")
            elif line.startswith("@@"):
                output.append(f"{CYAN}{line}{RESET}")
            else:
                output.append(line)
        return "\n".join(output)


def expectColorFile(content: str, language: str = "") -> str:
    """Apply syntax highlighting to file content.

    Args:
        content: File content text.
        language: Programming language for highlighting.

    Returns:
        Syntax-highlighted text or original if pygments unavailable.
    """
    if not content:
        return ""

    try:
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
        from pygments.formatters import TerminalTrueColorFormatter

        try:
            if language:
                lexer = get_lexer_by_name(language, stripall=True)
            else:
                lexer = guess_lexer(content)
        except Exception:
            lexer = TextLexer()

        formatter = TerminalTrueColorFormatter(style="monokai")
        return highlight(content, lexer, formatter).rstrip()

    except ImportError:
        return content
