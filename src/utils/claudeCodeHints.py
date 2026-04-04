"""JARVIS hints protocol.

CLIs and SDKs running under JARVIS can emit a self-closing
hint tag to stderr. The harness scans tool output for
these tags, strips them before the output reaches the model, and
surfaces an install prompt to the user.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Literal, Optional

logger = logging.getLogger(__name__)

JarvisHintType = Literal["plugin"]

SUPPORTED_VERSIONS = {1}
SUPPORTED_TYPES = {"plugin"}

HINT_TAG_RE = re.compile(
    r"^[ \t]*<jarvis-hint\s+([^>]*?)\s*/>[ \t]*$", re.MULTILINE
)
ATTR_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^\s/>]+))')


@dataclass
class JarvisHint:
    v: int
    type: JarvisHintType
    value: str
    source_command: str


def _parse_attrs(tag_body: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in ATTR_RE.finditer(tag_body):
        attrs[m.group(1)] = m.group(2) if m.group(2) is not None else (m.group(3) or "")
    return attrs


def _first_command_token(command: str) -> str:
    trimmed = command.strip()
    space_idx = -1
    for i, c in enumerate(trimmed):
        if c.isspace():
            space_idx = i
            break
    return trimmed if space_idx == -1 else trimmed[:space_idx]


def extract_jarvis_hints(
    output: str, command: str
) -> tuple[list[JarvisHint], str]:
    """Scan shell tool output for hint tags.

    Returns (hints, stripped_output).
    """
    if "<jarvis-hint" not in output:
        return [], output

    source_command = _first_command_token(command)
    hints: list[JarvisHint] = []

    def replacer(m: re.Match) -> str:
        raw_line = m.group(0)
        attrs = _parse_attrs(raw_line)
        try:
            v = int(attrs.get("v", "0"))
        except ValueError:
            v = 0
        hint_type = attrs.get("type", "")
        value = attrs.get("value", "")

        if v not in SUPPORTED_VERSIONS:
            logger.debug(f"[jarvisHints] dropped hint with unsupported v={v}")
            return ""
        if not hint_type or hint_type not in SUPPORTED_TYPES:
            logger.debug(
                f"[jarvisHints] dropped hint with unsupported type={hint_type}"
            )
            return ""
        if not value:
            logger.debug("[jarvisHints] dropped hint with empty value")
            return ""

        hints.append(
            JarvisHint(
                v=v,
                type=hint_type,  # type: ignore[arg-type]
                value=value,
                source_command=source_command,
            )
        )
        return ""

    stripped = HINT_TAG_RE.sub(replacer, output)

    if hints or stripped != output:
        stripped = re.sub(r"\n{3,}", "\n\n", stripped)

    return hints, stripped


# Pending hint store
_pending_hint: Optional[JarvisHint] = None
_shown_this_session = False
_pending_hint_subscribers: list[Callable[[], None]] = []


def _notify() -> None:
    for cb in _pending_hint_subscribers:
        cb()


def set_pending_hint(hint: JarvisHint) -> None:
    global _pending_hint
    if _shown_this_session:
        return
    _pending_hint = hint
    _notify()


def clear_pending_hint() -> None:
    global _pending_hint
    if _pending_hint is not None:
        _pending_hint = None
        _notify()


def mark_shown_this_session() -> None:
    global _shown_this_session
    _shown_this_session = True


def subscribe_to_pending_hint(callback: Callable[[], None]) -> Callable[[], None]:
    _pending_hint_subscribers.append(callback)

    def unsubscribe() -> None:
        if callback in _pending_hint_subscribers:
            _pending_hint_subscribers.remove(callback)

    return unsubscribe


def get_pending_hint_snapshot() -> Optional[JarvisHint]:
    return _pending_hint


def has_shown_hint_this_session() -> bool:
    return _shown_this_session


def reset_jarvis_hint_store() -> None:
    """Test-only reset."""
    global _pending_hint, _shown_this_session
    _pending_hint = None
    _shown_this_session = False
