"""
Early Input Capture

Captures terminal input typed before the REPL is fully initialized.
Users often start typing immediately, and those early keystrokes
would otherwise be lost during startup.

Usage:
1. Call start_capturing_early_input() as early as possible
2. When REPL is ready, call consume_early_input() to get buffered text
3. stop_capturing_early_input() is called automatically when input is consumed
"""

from __future__ import annotations

import sys
import threading
from typing import Optional, Callable

# Buffer for early input characters
_early_input_buffer: str = ""
# Flag to track if we're currently capturing
_is_capturing: bool = False
# Thread for reading input
_reader_thread: Optional[threading.Thread] = None
# Event to signal stop
_stop_event: threading.Event = threading.Event()


def _last_grapheme(text: str) -> str:
    """Get the last grapheme cluster from a string (simplified)."""
    if not text:
        return ""
    # Simplified: return last character. Full grapheme segmentation
    # would require a library like grapheme.
    return text[-1]


def start_capturing_early_input() -> None:
    """
    Start capturing stdin data early, before the REPL is initialized.
    Should be called as early as possible in the startup sequence.

    Only captures if stdin is a TTY (interactive terminal).
    """
    global _early_input_buffer, _is_capturing, _reader_thread, _stop_event

    if not sys.stdin.isatty() or _is_capturing:
        return

    if "-p" in sys.argv or "--print" in sys.argv:
        return

    _is_capturing = True
    _early_input_buffer = ""
    _stop_event = threading.Event()

    def _read_loop() -> None:
        global _early_input_buffer
        try:
            while not _stop_event.is_set():
                if _stop_event.wait(timeout=0.05):
                    break
                # Non-blocking read attempt
                if sys.stdin.readable():
                    ch = sys.stdin.read(1)
                    if ch:
                        _process_chunk(ch)
        except Exception:
            pass

    _reader_thread = threading.Thread(target=_read_loop, daemon=True)
    _reader_thread.start()


def _process_chunk(text: str) -> None:
    """Process a chunk of input data."""
    global _early_input_buffer

    i = 0
    while i < len(text):
        char = text[i]
        code = ord(char)

        # Ctrl+C (code 3) - stop capturing and exit
        if code == 3:
            stop_capturing_early_input()
            sys.exit(130)
            return

        # Ctrl+D (code 4) - EOF, stop capturing
        if code == 4:
            stop_capturing_early_input()
            return

        # Backspace (code 127 or 8)
        if code in (127, 8):
            if _early_input_buffer:
                last = _last_grapheme(_early_input_buffer)
                _early_input_buffer = _early_input_buffer[:-(len(last) or 1)]
            i += 1
            continue

        # Skip escape sequences (arrow keys, function keys, etc.)
        if code == 27:
            i += 1  # Skip the ESC character
            while i < len(text) and not (64 <= ord(text[i]) <= 126):
                i += 1
            if i < len(text):
                i += 1  # Skip the terminating byte
            continue

        # Skip other control characters (except tab and newline)
        if code < 32 and code not in (9, 10, 13):
            i += 1
            continue

        # Convert carriage return to newline
        if code == 13:
            _early_input_buffer += "\n"
            i += 1
            continue

        # Add printable characters to buffer
        _early_input_buffer += char
        i += 1


def stop_capturing_early_input() -> None:
    """
    Stop capturing early input.
    Called automatically when input is consumed, or can be called manually.
    """
    global _is_capturing, _reader_thread

    if not _is_capturing:
        return

    _is_capturing = False
    _stop_event.set()

    if _reader_thread is not None:
        _reader_thread.join(timeout=1.0)
        _reader_thread = None


def consume_early_input() -> str:
    """
    Consume any early input that was captured.
    Returns the captured input and clears the buffer.
    Automatically stops capturing when called.
    """
    global _early_input_buffer

    stop_capturing_early_input()
    result = _early_input_buffer.strip()
    _early_input_buffer = ""
    return result


def has_early_input() -> bool:
    """Check if there is any early input available without consuming it."""
    return len(_early_input_buffer.strip()) > 0


def seed_early_input(text: str) -> None:
    """
    Seed the early input buffer with text that will appear pre-filled
    in the prompt input when the REPL renders. Does not auto-submit.
    """
    global _early_input_buffer
    _early_input_buffer = text


def is_capturing_early_input() -> bool:
    """Check if early input capture is currently active."""
    return _is_capturing
