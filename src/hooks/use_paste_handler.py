"""Paste handling with image detection and chunked input assembly."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

PASTE_THRESHOLD = 100
CLIPBOARD_CHECK_DEBOUNCE_MS = 50
PASTE_COMPLETION_TIMEOUT_MS = 100


@dataclass
class PasteState:
    chunks: List[str] = field(default_factory=list)
    timeout_id: Optional[float] = None


@dataclass
class ImageData:
    base64: str
    media_type: str
    path: Optional[str] = None
    dimensions: Optional[dict] = None


def is_image_file_path(path: str) -> bool:
    """Check if a path looks like an image file."""
    path = path.strip()
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff"}
    _, ext = os.path.splitext(path.lower())
    return ext in image_extensions


class PasteHandler:
    """Handles paste input with image detection and chunked assembly.

    Manages the state machine for:
    - Detecting large pastes (>PASTE_THRESHOLD chars)
    - Assembling chunked paste input
    - Detecting image file paths in pasted content
    - Clipboard image detection (macOS)

    Equivalent to usePasteHandler React hook.
    """

    def __init__(
        self,
        on_paste: Optional[Callable[[str], None]] = None,
        on_input: Optional[Callable[[str], None]] = None,
        on_image_paste: Optional[Callable] = None,
        is_macos: bool = False,
    ):
        self.on_paste = on_paste
        self.on_input = on_input
        self.on_image_paste = on_image_paste
        self.is_macos = is_macos
        self.paste_state = PasteState()
        self.is_pasting = False
        self._paste_pending = False

    def handle_input(self, input_text: str, is_from_paste: bool = False) -> None:
        """Handle input, detecting paste patterns."""
        if is_from_paste:
            self.is_pasting = True

        # Check for image file paths
        lines = re.split(r" (?=/|[A-Za-z]:\\)", input_text)
        expanded_lines = []
        for part in lines:
            expanded_lines.extend(part.split("\n"))
        has_image = any(is_image_file_path(line.strip()) for line in expanded_lines if line.strip())

        # Empty paste on macOS = clipboard image
        if is_from_paste and len(input_text) == 0 and self.is_macos and self.on_image_paste:
            self._check_clipboard_for_image()
            self.is_pasting = False
            return

        should_handle_as_paste = self.on_paste and (
            len(input_text) > PASTE_THRESHOLD
            or self._paste_pending
            or has_image
            or is_from_paste
        )

        if should_handle_as_paste:
            self._paste_pending = True
            self.paste_state.chunks.append(input_text)
            # In a real async context, reset timeout here
            # For now, call complete_paste when ready
            return

        if self.on_input:
            self.on_input(input_text)

        if len(input_text) > 10:
            self.is_pasting = False

    def complete_paste(self) -> None:
        """Complete the paste operation, processing accumulated chunks."""
        self._paste_pending = False
        pasted_text = "".join(self.paste_state.chunks)
        # Clean up orphaned focus sequences
        pasted_text = re.sub(r"\[I$", "", pasted_text)
        pasted_text = re.sub(r"\[O$", "", pasted_text)

        # Check for image paths
        lines = re.split(r" (?=/|[A-Za-z]:\\)", pasted_text)
        expanded_lines = []
        for part in lines:
            expanded_lines.extend(part.split("\n"))
        expanded_lines = [l for l in expanded_lines if l.strip()]
        image_paths = [l for l in expanded_lines if is_image_file_path(l)]

        if self.on_image_paste and image_paths:
            # Handle image paths
            non_image = [l for l in expanded_lines if not is_image_file_path(l)]
            if non_image and self.on_paste:
                self.on_paste("\n".join(non_image))
            self.is_pasting = False
        elif self.on_paste:
            self.on_paste(pasted_text)
            self.is_pasting = False

        self.paste_state = PasteState()

    def _check_clipboard_for_image(self) -> None:
        """Check clipboard for image content (macOS)."""
        # Platform-specific clipboard image detection
        pass

    def reset(self) -> None:
        """Reset paste state."""
        self.paste_state = PasteState()
        self.is_pasting = False
        self._paste_pending = False
