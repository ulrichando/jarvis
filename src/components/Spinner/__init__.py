"""Terminal spinner with braille animation and elapsed time.

Features:
- Braille spinner frames
- Dots spinner frames
- Elapsed time display (after 2s)
- Current action text
- Thread-safe start/stop
- Optional shimmer effect for long waits
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional, Callable

from src.components.Spinner.utils import (
    getDefaultCharacters,
    interpolateColor,
    toRGBColor,
    hueToRgb,
    parseRGB,
)

# ANSI codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"
GREY = "\033[90m"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
CLEAR_LINE = "\033[2K\r"

# Spinner frame sets
BRAILLE_FRAMES = list("\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f")
DOTS_FRAMES = list("\u28fe\u28fd\u28fb\u28bf\u287f\u28df\u28ef\u28f7")
LINE_FRAMES = ["|", "/", "-", "\\"]
BLOCK_FRAMES = ["\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a", "\u2589", "\u2588",
                "\u2589", "\u258a", "\u258b", "\u258c", "\u258d", "\u258e", "\u258f"]

# Shimmer color gradient
SHIMMER_COLORS = [
    "\033[38;5;240m", "\033[38;5;241m", "\033[38;5;242m", "\033[38;5;243m",
    "\033[38;5;244m", "\033[38;5;245m", "\033[38;5;246m", "\033[38;5;245m",
    "\033[38;5;244m", "\033[38;5;243m", "\033[38;5;242m", "\033[38;5;241m",
]


def _format_elapsed(seconds: float) -> str:
    """Format elapsed time for display."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h{minutes:02d}m"


class Spinner:
    """Thread-safe animated terminal spinner.

    Usage:
        spinner = Spinner("Loading")
        spinner.start()
        # ... do work ...
        spinner.update("Processing file.py")
        # ... more work ...
        spinner.stop("Done!")

    Or as context manager:
        with Spinner("Loading") as s:
            s.update("Step 1")
    """

    def __init__(
        self,
        text: str = "",
        frames: Optional[list[str]] = None,
        interval: float = 0.08,
        color: str = CYAN,
        show_elapsed: bool = True,
        elapsed_threshold: float = 2.0,
        shimmer: bool = True,
        stream=None,
    ):
        self.text = text
        self.frames = frames or BRAILLE_FRAMES
        self.interval = interval
        self.color = color
        self.show_elapsed = show_elapsed
        self.elapsed_threshold = elapsed_threshold
        self.shimmer = shimmer
        self.stream = stream or sys.stderr

        self._frame_idx = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._start_time = 0.0
        self._stop_event = threading.Event()

    def start(self) -> "Spinner":
        """Start the spinner animation."""
        if self._running:
            return self
        self._running = True
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self.stream.write(HIDE_CURSOR)
        self.stream.flush()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()
        return self

    def stop(self, final_text: str = "") -> None:
        """Stop the spinner and optionally show final text."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.stream.write(CLEAR_LINE)
        if final_text:
            self.stream.write(f"  {final_text}\n")
        self.stream.write(SHOW_CURSOR)
        self.stream.flush()

    def update(self, text: str) -> None:
        """Update the spinner action text (thread-safe)."""
        with self._lock:
            self.text = text

    def _animate(self) -> None:
        """Animation loop (runs in background thread)."""
        while not self._stop_event.is_set():
            with self._lock:
                text = self.text

            elapsed = time.monotonic() - self._start_time
            frame = self.frames[self._frame_idx % len(self.frames)]
            self._frame_idx += 1

            parts = [CLEAR_LINE, "  "]

            if self.shimmer and elapsed > 10:
                shimmer_idx = self._frame_idx % len(SHIMMER_COLORS)
                parts.append(f"{SHIMMER_COLORS[shimmer_idx]}{frame}{RESET}")
            else:
                parts.append(f"{self.color}{frame}{RESET}")

            if text:
                parts.append(f" {text}")

            if self.show_elapsed and elapsed >= self.elapsed_threshold:
                parts.append(f" {DIM}{_format_elapsed(elapsed)}{RESET}")

            line = "".join(parts)
            self.stream.write(line)
            self.stream.flush()

            self._stop_event.wait(self.interval)

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()


class BriefSpinner(Spinner):
    """Compact spinner using dots frames."""

    def __init__(self, text: str = "", **kwargs):
        kwargs.setdefault("frames", DOTS_FRAMES)
        kwargs.setdefault("interval", 0.1)
        super().__init__(text=text, **kwargs)


class SpinnerWithVerb(Spinner):
    """Spinner that displays a verb/action prominently."""

    def __init__(self, verb: str = "Working", text: str = "", **kwargs):
        self.verb = verb
        display = f"{BOLD}{verb}{RESET} {text}" if text else f"{BOLD}{verb}{RESET}"
        super().__init__(text=display, **kwargs)

    def update(self, text: str, verb: Optional[str] = None) -> None:
        if verb:
            self.verb = verb
        with self._lock:
            if self.verb:
                self.text = f"{BOLD}{self.verb}{RESET} {text}" if text else f"{BOLD}{self.verb}{RESET}"
            else:
                self.text = text


def SpinnerWithVerbInner(verb: str = "Working", text: str = "", **kwargs) -> SpinnerWithVerb:
    """Create a SpinnerWithVerb instance."""
    return SpinnerWithVerb(verb=verb, text=text, **kwargs)


def BriefIdleStatus(text: str = "Idle") -> str:
    """Return a static idle status line (no animation)."""
    return f"  {DIM}{text}{RESET}"


def findNextPendingTask(tasks: list[dict]) -> Optional[dict]:
    """Find the next pending task from a task list."""
    for task in tasks:
        if task.get("status") == "pending":
            return task
    return None


class Props:
    """Properties for spinner configuration."""
    def __init__(self, text: str = "", frames: Optional[list[str]] = None):
        self.text = text
        self.frames = frames


class BriefSpinnerProps:
    """Properties for brief spinner."""
    def __init__(self, text: str = ""):
        self.text = text
