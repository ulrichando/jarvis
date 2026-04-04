"""Terminal prompt input utilities.

Provides interactive text input, confirmation dialogs, and choice prompts.
Uses raw tty input for single-key operations, readline for text input.
Provides terminal prompt input components.
"""

import os
import sys
import tty
import termios
from typing import Optional


def _read_char(fd: int) -> str:
    """Read a single character from raw terminal."""
    ch = os.read(fd, 1)
    if not ch:
        return ""
    return ch.decode("utf-8", errors="replace")


def prompt_input(
    prompt_text: str,
    multiline: bool = False,
    history: Optional[list[str]] = None,
) -> str:
    """Prompt the user for text input.

    Args:
        prompt_text: The prompt label to display.
        multiline: If True, allow multiple lines (end with Ctrl+D or empty line).
        history: Optional list of previous inputs for up-arrow recall.

    Returns:
        The entered text string. Empty string if nothing entered.

    In single-line mode, uses Python's built-in input() with readline support.
    In multiline mode, reads until an empty line or EOF (Ctrl+D).
    """
    if not multiline:
        # Single-line: use standard input with optional history
        if history is not None:
            try:
                import readline

                # Temporarily install history
                old_len = readline.get_current_history_length()
                for item in history:
                    readline.add_history(item)

                try:
                    result = input(f"{prompt_text} ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return ""

                # Restore old history state
                new_len = readline.get_current_history_length()
                for _ in range(new_len - old_len):
                    try:
                        readline.remove_history_item(old_len)
                    except ValueError:
                        break

                return result
            except ImportError:
                pass

        try:
            return input(f"{prompt_text} ")
        except (EOFError, KeyboardInterrupt):
            print()
            return ""
    else:
        # Multiline: read lines until empty line or Ctrl+D
        print(f"{prompt_text} (end with empty line or Ctrl+D)")
        lines = []
        try:
            while True:
                line = input("  ")
                if line == "":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass
        return "\n".join(lines)


def confirm(message: str, default: bool = True) -> bool:
    """Prompt the user for a yes/no confirmation.

    Args:
        message: The question to display.
        default: Default answer when Enter is pressed (True=yes, False=no).

    Returns:
        True for yes, False for no.

    Example::

        >>> if confirm("Delete all files?", default=False):
        ...     delete_files()
    """
    hint = "Y/n" if default else "y/N"
    prompt = f"{message} [{hint}] "

    fd = sys.stdin.fileno()

    # Check if stdin is a tty for raw mode
    if not os.isatty(fd):
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if answer == "":
            return default
        return answer in ("y", "yes")

    old_settings = termios.tcgetattr(fd)
    try:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        tty.setcbreak(fd)

        ch = _read_char(fd)

        if ch in ("\r", "\n", ""):
            result = default
            sys.stdout.write("yes\n" if result else "no\n")
        elif ch.lower() == "y":
            result = True
            sys.stdout.write("yes\n")
        elif ch.lower() == "n":
            result = False
            sys.stdout.write("no\n")
        elif ch == "\x03":
            # Ctrl+C
            sys.stdout.write("\n")
            result = default
        else:
            result = default
            sys.stdout.write("yes\n" if result else "no\n")

        sys.stdout.flush()
        return result
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def prompt_choice(message: str, choices: list[str]) -> Optional[str]:
    """Prompt the user to select from a list of string choices.

    Uses the select utility for an interactive menu. Falls back to
    numbered input if not running in a tty.

    Args:
        message: The prompt message.
        choices: List of string options to choose from.

    Returns:
        The selected string, or None if cancelled.

    Example::

        >>> color = prompt_choice("Pick a color:", ["red", "green", "blue"])
    """
    if not choices:
        return None

    fd = sys.stdin.fileno()

    if not os.isatty(fd):
        # Fallback for non-interactive
        print(message)
        for i, choice in enumerate(choices, 1):
            print(f"  {i}. {choice}")
        try:
            answer = input("Enter number: ").strip()
            idx = int(answer) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        return None

    # Use the select module for interactive picking
    from src.brain_utils.select import select

    options = [{"label": c, "value": c} for c in choices]
    return select(options, prompt=message)
