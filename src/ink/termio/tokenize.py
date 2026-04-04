"""Input Tokenizer - Escape sequence boundary detection.

Splits terminal input into tokens: text chunks and raw escape sequences.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .ansi import C0, ESC_TYPE, is_esc_final
from .csi import is_csi_final, is_csi_intermediate, is_csi_param


@dataclass
class Token:
    """A token from the tokenizer."""
    type: Literal["text", "sequence"]
    value: str


State = Literal[
    "ground", "escape", "escapeIntermediate", "csi", "ss3", "osc", "dcs", "apc"
]


@dataclass
class _InternalState:
    state: State = "ground"
    buffer: str = ""


class Tokenizer:
    """Streaming tokenizer for terminal input."""

    def __init__(self, x10_mouse: bool = False) -> None:
        self._state: State = "ground"
        self._buffer: str = ""
        self._x10_mouse = x10_mouse

    def feed(self, input_: str) -> list[Token]:
        result = _tokenize(input_, self._state, self._buffer, False, self._x10_mouse)
        self._state = result["state"].state
        self._buffer = result["state"].buffer
        return result["tokens"]

    def flush(self) -> list[Token]:
        result = _tokenize("", self._state, self._buffer, True, self._x10_mouse)
        self._state = result["state"].state
        self._buffer = result["state"].buffer
        return result["tokens"]

    def reset(self) -> None:
        self._state = "ground"
        self._buffer = ""

    def buffer(self) -> str:
        return self._buffer


def create_tokenizer(x10_mouse: bool = False) -> Tokenizer:
    """Create a streaming tokenizer for terminal input."""
    return Tokenizer(x10_mouse=x10_mouse)


def _tokenize(
    input_: str,
    initial_state: State,
    initial_buffer: str,
    flush: bool,
    x10_mouse: bool,
) -> dict:
    tokens: list[Token] = []
    result = _InternalState(state=initial_state, buffer="")

    data = initial_buffer + input_
    i = 0
    text_start = 0
    seq_start = 0

    def flush_text() -> None:
        nonlocal text_start
        if i > text_start:
            text = data[text_start:i]
            if text:
                tokens.append(Token(type="text", value=text))
        text_start = i

    def emit_sequence(seq: str) -> None:
        nonlocal text_start
        if seq:
            tokens.append(Token(type="sequence", value=seq))
        result.state = "ground"
        text_start = i

    while i < len(data):
        code = ord(data[i])

        if result.state == "ground":
            if code == C0.ESC:
                flush_text()
                seq_start = i
                result.state = "escape"
                i += 1
            else:
                i += 1

        elif result.state == "escape":
            if code == ESC_TYPE.CSI:
                result.state = "csi"
                i += 1
            elif code == ESC_TYPE.OSC:
                result.state = "osc"
                i += 1
            elif code == ESC_TYPE.DCS:
                result.state = "dcs"
                i += 1
            elif code == ESC_TYPE.APC:
                result.state = "apc"
                i += 1
            elif code == 0x4F:  # 'O' - SS3
                result.state = "ss3"
                i += 1
            elif is_csi_intermediate(code):
                result.state = "escapeIntermediate"
                i += 1
            elif is_esc_final(code):
                i += 1
                emit_sequence(data[seq_start:i])
            elif code == C0.ESC:
                emit_sequence(data[seq_start:i])
                seq_start = i
                result.state = "escape"
                i += 1
            else:
                result.state = "ground"
                text_start = seq_start

        elif result.state == "escapeIntermediate":
            if is_csi_intermediate(code):
                i += 1
            elif is_esc_final(code):
                i += 1
                emit_sequence(data[seq_start:i])
            else:
                result.state = "ground"
                text_start = seq_start

        elif result.state == "csi":
            if (
                x10_mouse
                and code == 0x4D  # M
                and i - seq_start == 2
                and (i + 1 >= len(data) or ord(data[i + 1]) >= 0x20)
                and (i + 2 >= len(data) or ord(data[i + 2]) >= 0x20)
                and (i + 3 >= len(data) or ord(data[i + 3]) >= 0x20)
            ):
                if i + 4 <= len(data):
                    i += 4
                    emit_sequence(data[seq_start:i])
                else:
                    i = len(data)
            elif is_csi_final(code):
                i += 1
                emit_sequence(data[seq_start:i])
            elif is_csi_param(code) or is_csi_intermediate(code):
                i += 1
            else:
                result.state = "ground"
                text_start = seq_start

        elif result.state == "ss3":
            if 0x40 <= code <= 0x7E:
                i += 1
                emit_sequence(data[seq_start:i])
            else:
                result.state = "ground"
                text_start = seq_start

        elif result.state == "osc":
            if code == C0.BEL:
                i += 1
                emit_sequence(data[seq_start:i])
            elif (
                code == C0.ESC
                and i + 1 < len(data)
                and ord(data[i + 1]) == ESC_TYPE.ST
            ):
                i += 2
                emit_sequence(data[seq_start:i])
            else:
                i += 1

        elif result.state in ("dcs", "apc"):
            if code == C0.BEL:
                i += 1
                emit_sequence(data[seq_start:i])
            elif (
                code == C0.ESC
                and i + 1 < len(data)
                and ord(data[i + 1]) == ESC_TYPE.ST
            ):
                i += 2
                emit_sequence(data[seq_start:i])
            else:
                i += 1

    # Handle end of input
    if result.state == "ground":
        flush_text()
    elif flush:
        remaining = data[seq_start:]
        if remaining:
            tokens.append(Token(type="sequence", value=remaining))
        result.state = "ground"
    else:
        result.buffer = data[seq_start:]

    return {"tokens": tokens, "state": result}
