"""ANSI Parser - Semantic Action Generator.

A streaming parser for ANSI escape sequences that produces semantic actions.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Generator

from .ansi import C0
from .csi import CSI, CURSOR_STYLES, ERASE_DISPLAY, ERASE_LINE_REGION
from .dec import DEC
from .esc import parse_esc
from .osc import parse_osc
from .sgr import apply_sgr
from .tokenize import Token, Tokenizer, create_tokenizer
from .types import Grapheme, TextStyle, default_style


# =============================================================================
# Grapheme Utilities
# =============================================================================

def _is_emoji(code_point: int) -> bool:
    return (
        (0x2600 <= code_point <= 0x26FF)
        or (0x2700 <= code_point <= 0x27BF)
        or (0x1F300 <= code_point <= 0x1F9FF)
        or (0x1FA00 <= code_point <= 0x1FAFF)
        or (0x1F1E0 <= code_point <= 0x1F1FF)
    )


def _is_east_asian_wide(code_point: int) -> bool:
    return (
        (0x1100 <= code_point <= 0x115F)
        or (0x2E80 <= code_point <= 0x9FFF)
        or (0xAC00 <= code_point <= 0xD7A3)
        or (0xF900 <= code_point <= 0xFAFF)
        or (0xFE10 <= code_point <= 0xFE1F)
        or (0xFE30 <= code_point <= 0xFE6F)
        or (0xFF00 <= code_point <= 0xFF60)
        or (0xFFE0 <= code_point <= 0xFFE6)
        or (0x20000 <= code_point <= 0x2FFFD)
        or (0x30000 <= code_point <= 0x3FFFD)
    )


def _has_multiple_codepoints(s: str) -> bool:
    count = 0
    for _ in s:
        count += 1
        if count > 1:
            return True
    return False


def _grapheme_width(grapheme: str) -> int:
    if _has_multiple_codepoints(grapheme):
        return 2
    cp = ord(grapheme[0]) if grapheme else 0
    if _is_emoji(cp) or _is_east_asian_wide(cp):
        return 2
    return 1


def _segment_graphemes(text: str) -> Generator[Grapheme, None, None]:
    """Segment text into graphemes. Simple implementation."""
    # Python doesn't have Intl.Segmenter built-in, use character iteration
    # For a proper implementation, use the `grapheme` package
    i = 0
    while i < len(text):
        ch = text[i]
        # Handle surrogate pairs / multi-char sequences
        if ord(ch) >= 0xD800 and ord(ch) <= 0xDBFF and i + 1 < len(text):
            ch = text[i:i+2]
            i += 2
        else:
            i += 1
        yield Grapheme(value=ch, width=_grapheme_width(ch))


# =============================================================================
# Sequence Parsing
# =============================================================================

def _parse_csi_params(param_str: str) -> list[int]:
    if param_str == "":
        return []
    return [0 if s == "" else int(s) for s in re.split(r"[;:]", param_str)]


def _parse_csi(raw_sequence: str) -> dict[str, Any] | None:
    """Parse a raw CSI sequence into an action."""
    inner = raw_sequence[2:]
    if not inner:
        return None

    final_byte = ord(inner[-1])
    before_final = inner[:-1]

    private_mode = ""
    param_str = before_final
    intermediate = ""

    if before_final and before_final[0] in "?>=":
        private_mode = before_final[0]
        param_str = before_final[1:]

    intermediate_match = re.search(r"([^0-9;:]+)$", param_str)
    if intermediate_match:
        intermediate = intermediate_match.group(1)
        param_str = param_str[:-len(intermediate)]

    params = _parse_csi_params(param_str)
    p0 = params[0] if params else 1
    p1 = params[1] if len(params) > 1 else 1

    # SGR
    if final_byte == CSI.SGR and private_mode == "":
        return {"type": "sgr", "params": param_str}

    # Cursor movement
    if final_byte == CSI.CUU:
        return {"type": "cursor", "action": {"type": "move", "direction": "up", "count": p0}}
    if final_byte == CSI.CUD:
        return {"type": "cursor", "action": {"type": "move", "direction": "down", "count": p0}}
    if final_byte == CSI.CUF:
        return {"type": "cursor", "action": {"type": "move", "direction": "forward", "count": p0}}
    if final_byte == CSI.CUB:
        return {"type": "cursor", "action": {"type": "move", "direction": "back", "count": p0}}
    if final_byte == CSI.CNL:
        return {"type": "cursor", "action": {"type": "nextLine", "count": p0}}
    if final_byte == CSI.CPL:
        return {"type": "cursor", "action": {"type": "prevLine", "count": p0}}
    if final_byte == CSI.CHA:
        return {"type": "cursor", "action": {"type": "column", "col": p0}}
    if final_byte in (CSI.CUP, CSI.HVP):
        return {"type": "cursor", "action": {"type": "position", "row": p0, "col": p1}}
    if final_byte == CSI.VPA:
        return {"type": "cursor", "action": {"type": "row", "row": p0}}

    # Erase
    if final_byte == CSI.ED:
        idx = params[0] if params else 0
        region = ERASE_DISPLAY[idx] if idx < len(ERASE_DISPLAY) else "toEnd"
        return {"type": "erase", "action": {"type": "display", "region": region}}
    if final_byte == CSI.EL:
        idx = params[0] if params else 0
        region = ERASE_LINE_REGION[idx] if idx < len(ERASE_LINE_REGION) else "toEnd"
        return {"type": "erase", "action": {"type": "line", "region": region}}
    if final_byte == CSI.ECH:
        return {"type": "erase", "action": {"type": "chars", "count": p0}}

    # Scroll
    if final_byte == CSI.SU:
        return {"type": "scroll", "action": {"type": "up", "count": p0}}
    if final_byte == CSI.SD:
        return {"type": "scroll", "action": {"type": "down", "count": p0}}
    if final_byte == CSI.DECSTBM:
        return {"type": "scroll", "action": {"type": "setRegion", "top": p0, "bottom": p1}}

    # Cursor save/restore
    if final_byte == CSI.SCOSC:
        return {"type": "cursor", "action": {"type": "save"}}
    if final_byte == CSI.SCORC:
        return {"type": "cursor", "action": {"type": "restore"}}

    # Cursor style
    if final_byte == CSI.DECSCUSR and intermediate == " ":
        style_info = CURSOR_STYLES[p0] if p0 < len(CURSOR_STYLES) else CURSOR_STYLES[0]
        return {"type": "cursor", "action": {"type": "style", **style_info}}

    # Private modes
    if private_mode == "?" and final_byte in (CSI.SM, CSI.RM):
        enabled = final_byte == CSI.SM

        if p0 == DEC.CURSOR_VISIBLE:
            return {"type": "cursor", "action": {"type": "show"} if enabled else {"type": "hide"}}
        if p0 in (DEC.ALT_SCREEN_CLEAR, DEC.ALT_SCREEN):
            return {"type": "mode", "action": {"type": "alternateScreen", "enabled": enabled}}
        if p0 == DEC.BRACKETED_PASTE:
            return {"type": "mode", "action": {"type": "bracketedPaste", "enabled": enabled}}
        if p0 == DEC.MOUSE_NORMAL:
            return {"type": "mode", "action": {"type": "mouseTracking", "mode": "normal" if enabled else "off"}}
        if p0 == DEC.MOUSE_BUTTON:
            return {"type": "mode", "action": {"type": "mouseTracking", "mode": "button" if enabled else "off"}}
        if p0 == DEC.MOUSE_ANY:
            return {"type": "mode", "action": {"type": "mouseTracking", "mode": "any" if enabled else "off"}}
        if p0 == DEC.FOCUS_EVENTS:
            return {"type": "mode", "action": {"type": "focusEvents", "enabled": enabled}}

    return {"type": "unknown", "sequence": raw_sequence}


def _identify_sequence(seq: str) -> str:
    """Identify the type of escape sequence."""
    if len(seq) < 2:
        return "unknown"
    if ord(seq[0]) != C0.ESC:
        return "unknown"
    second = ord(seq[1])
    if second == 0x5B:
        return "csi"
    if second == 0x5D:
        return "osc"
    if second == 0x4F:
        return "ss3"
    return "esc"


class Parser:
    """Streaming ANSI parser that produces semantic actions."""

    def __init__(self) -> None:
        self._tokenizer = create_tokenizer()
        self.style: TextStyle = default_style()
        self.in_link: bool = False
        self.link_url: str | None = None

    def reset(self) -> None:
        self._tokenizer.reset()
        self.style = default_style()
        self.in_link = False
        self.link_url = None

    def feed(self, input_: str) -> list[dict[str, Any]]:
        """Feed input and get resulting actions."""
        tokens = self._tokenizer.feed(input_)
        actions: list[dict[str, Any]] = []
        for token in tokens:
            actions.extend(self._process_token(token))
        return actions

    def _process_token(self, token: Token) -> list[dict[str, Any]]:
        if token.type == "text":
            return self._process_text(token.value)
        return self._process_sequence(token.value)

    def _process_text(self, text: str) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        current = ""

        for char in text:
            if ord(char) == C0.BEL:
                if current:
                    graphemes = list(_segment_graphemes(current))
                    if graphemes:
                        actions.append({
                            "type": "text",
                            "graphemes": graphemes,
                            "style": self.style.copy(),
                        })
                    current = ""
                actions.append({"type": "bell"})
            else:
                current += char

        if current:
            graphemes = list(_segment_graphemes(current))
            if graphemes:
                actions.append({
                    "type": "text",
                    "graphemes": graphemes,
                    "style": self.style.copy(),
                })

        return actions

    def _process_sequence(self, seq: str) -> list[dict[str, Any]]:
        seq_type = _identify_sequence(seq)

        if seq_type == "csi":
            action = _parse_csi(seq)
            if not action:
                return []
            if action["type"] == "sgr":
                self.style = apply_sgr(action["params"], self.style)
                return []
            return [action]

        if seq_type == "osc":
            content = seq[2:]
            if content.endswith("\x07"):
                content = content[:-1]
            elif content.endswith("\x1b\\"):
                content = content[:-2]
            action = parse_osc(content)
            if action:
                if action["type"] == "link":
                    if action["action"]["type"] == "start":
                        self.in_link = True
                        self.link_url = action["action"]["url"]
                    else:
                        self.in_link = False
                        self.link_url = None
                return [action]
            return []

        if seq_type == "esc":
            esc_content = seq[1:]
            action = parse_esc(esc_content)
            return [action] if action else []

        if seq_type == "ss3":
            return [{"type": "unknown", "sequence": seq}]

        return [{"type": "unknown", "sequence": seq}]
