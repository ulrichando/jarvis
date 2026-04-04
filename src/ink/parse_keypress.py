"""Keyboard input parser - converts terminal input to key events."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .termio.csi import PASTE_START, PASTE_END
from .termio.tokenize import Tokenizer, create_tokenizer

META_KEY_CODE_RE = re.compile(r"^(?:\x1b)([a-zA-Z0-9])$")
FN_KEY_RE = re.compile(
    r"^(?:\x1b+)(O|N|\[|\[\[)(?:(\d+)(?:;(\d+))?([~^$])|(?:1;)?(\d+)?([a-zA-Z]))"
)
CSI_U_RE = re.compile(r"^\x1b\[(\d+)(?:;(\d+))?u")
MODIFY_OTHER_KEYS_RE = re.compile(r"^\x1b\[27;(\d+);(\d+)~")
DECRPM_RE = re.compile(r"^\x1b\[\?(\d+);(\d+)\$y$")
DA1_RE = re.compile(r"^\x1b\[\?([\d;]*)c$")
DA2_RE = re.compile(r"^\x1b\[>([\d;]*)c$")
KITTY_FLAGS_RE = re.compile(r"^\x1b\[\?(\d+)u$")
CURSOR_POSITION_RE = re.compile(r"^\x1b\[\?(\d+);(\d+)R$")
OSC_RESPONSE_RE = re.compile(r"^\x1b\](\d+);(.*?)(?:\x07|\x1b\\)$", re.DOTALL)
XTVERSION_RE = re.compile(r"^\x1bP>\|(.*?)(?:\x07|\x1b\\)$", re.DOTALL)
SGR_MOUSE_RE = re.compile(r"^\x1b\[<(\d+);(\d+);(\d+)([Mm])$")


@dataclass
class ParsedKey:
    kind: str = "key"
    fn: bool = False
    name: str | None = ""
    ctrl: bool = False
    meta: bool = False
    shift: bool = False
    option: bool = False
    super_: bool = False
    sequence: str | None = ""
    raw: str | None = ""
    code: str | None = None
    is_pasted: bool = False


@dataclass
class ParsedResponse:
    kind: str = "response"
    sequence: str = ""
    response: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedMouse:
    kind: str = "mouse"
    button: int = 0
    action: str = "press"
    col: int = 0
    row: int = 0
    sequence: str = ""


ParsedInput = ParsedKey | ParsedMouse | ParsedResponse

KEY_NAME: dict[str, str] = {
    "OP": "f1", "OQ": "f2", "OR": "f3", "OS": "f4",
    "Op": "0", "Oq": "1", "Or": "2", "Os": "3", "Ot": "4",
    "Ou": "5", "Ov": "6", "Ow": "7", "Ox": "8", "Oy": "9",
    "Oj": "*", "Ok": "+", "Ol": ",", "Om": "-", "On": ".", "Oo": "/",
    "OM": "return",
    "[11~": "f1", "[12~": "f2", "[13~": "f3", "[14~": "f4",
    "[[A": "f1", "[[B": "f2", "[[C": "f3", "[[D": "f4", "[[E": "f5",
    "[15~": "f5", "[17~": "f6", "[18~": "f7", "[19~": "f8",
    "[20~": "f9", "[21~": "f10", "[23~": "f11", "[24~": "f12",
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "[E": "clear", "[F": "end", "[H": "home",
    "OA": "up", "OB": "down", "OC": "right", "OD": "left",
    "OE": "clear", "OF": "end", "OH": "home",
    "[1~": "home", "[2~": "insert", "[3~": "delete", "[4~": "end",
    "[5~": "pageup", "[6~": "pagedown",
    "[[5~": "pageup", "[[6~": "pagedown",
    "[7~": "home", "[8~": "end",
    "[a": "up", "[b": "down", "[c": "right", "[d": "left", "[e": "clear",
    "[2$": "insert", "[3$": "delete", "[5$": "pageup", "[6$": "pagedown",
    "[7$": "home", "[8$": "end",
    "Oa": "up", "Ob": "down", "Oc": "right", "Od": "left", "Oe": "clear",
    "[2^": "insert", "[3^": "delete", "[5^": "pageup", "[6^": "pagedown",
    "[7^": "home", "[8^": "end",
    "[Z": "tab",
}

non_alphanumeric_keys = list({v for v in KEY_NAME.values() if len(v) > 1}) + [
    "escape", "backspace", "wheelup", "wheeldown", "mouse",
]

DECRPM_STATUS = {
    "NOT_RECOGNIZED": 0, "SET": 1, "RESET": 2,
    "PERMANENTLY_SET": 3, "PERMANENTLY_RESET": 4,
}


def _decode_modifier(modifier: int) -> dict[str, bool]:
    m = modifier - 1
    return {
        "shift": bool(m & 1),
        "meta": bool(m & 2),
        "ctrl": bool(m & 4),
        "super": bool(m & 8),
    }


def _keycode_to_name(keycode: int) -> str | None:
    mapping = {
        9: "tab", 13: "return", 27: "escape", 32: "space", 127: "backspace",
        57399: "0", 57400: "1", 57401: "2", 57402: "3", 57403: "4",
        57404: "5", 57405: "6", 57406: "7", 57407: "8", 57408: "9",
        57409: ".", 57410: "/", 57411: "*", 57412: "-", 57413: "+",
        57414: "return", 57415: "=",
    }
    if keycode in mapping:
        return mapping[keycode]
    if 32 <= keycode <= 126:
        return chr(keycode).lower()
    return None


@dataclass
class KeyParseState:
    mode: str = "NORMAL"
    incomplete: str = ""
    paste_buffer: str = ""
    _tokenizer: Tokenizer | None = None


INITIAL_STATE = KeyParseState()


def parse_keypress(s: str = "") -> ParsedKey:
    """Parse a single keypress sequence."""
    key = ParsedKey(sequence=s, raw=s)

    # CSI u (kitty keyboard protocol)
    match = CSI_U_RE.match(s)
    if match:
        codepoint = int(match.group(1))
        modifier = int(match.group(2)) if match.group(2) else 1
        mods = _decode_modifier(modifier)
        name = _keycode_to_name(codepoint)
        return ParsedKey(name=name, ctrl=mods["ctrl"], meta=mods["meta"],
                        shift=mods["shift"], super_=mods["super"],
                        sequence=s, raw=s)

    # modifyOtherKeys
    match = MODIFY_OTHER_KEYS_RE.match(s)
    if match:
        mods = _decode_modifier(int(match.group(1)))
        name = _keycode_to_name(int(match.group(2)))
        return ParsedKey(name=name, ctrl=mods["ctrl"], meta=mods["meta"],
                        shift=mods["shift"], super_=mods["super"],
                        sequence=s, raw=s)

    # SGR mouse
    match = SGR_MOUSE_RE.match(s)
    if match:
        button = int(match.group(1))
        if (button & 0x43) == 0x40:
            return ParsedKey(name="wheelup", sequence=s, raw=s)
        if (button & 0x43) == 0x41:
            return ParsedKey(name="wheeldown", sequence=s, raw=s)
        return ParsedKey(name="mouse", sequence=s, raw=s)

    # Simple keys
    if s == "\r":
        key.name = "return"
        key.raw = None
    elif s == "\n":
        key.name = "enter"
    elif s == "\t":
        key.name = "tab"
    elif s in ("\b", "\x1b\b"):
        key.name = "backspace"
        key.meta = s[0] == "\x1b"
    elif s in ("\x7f", "\x1b\x7f"):
        key.name = "backspace"
        key.meta = s[0] == "\x1b"
    elif s in ("\x1b", "\x1b\x1b"):
        key.name = "escape"
        key.meta = len(s) == 2
    elif s in (" ", "\x1b "):
        key.name = "space"
        key.meta = len(s) == 2
    elif s == "\x1f":
        key.name = "_"
        key.ctrl = True
    elif len(s) == 1 and s <= "\x1a":
        key.name = chr(ord(s) + ord("a") - 1)
        key.ctrl = True
    elif len(s) == 1 and "a" <= s <= "z":
        key.name = s
    elif len(s) == 1 and "A" <= s <= "Z":
        key.name = s.lower()
        key.shift = True
    else:
        # Function key matching
        match = FN_KEY_RE.match(s)
        if match:
            parts = [match.group(1), match.group(2), match.group(4), match.group(6)]
            code = "".join(p for p in parts if p)
            modifier = int(match.group(3) or match.group(5) or 1) - 1
            key.ctrl = bool(modifier & 4)
            key.meta = bool(modifier & 2)
            key.super_ = bool(modifier & 8)
            key.shift = bool(modifier & 1)
            key.code = code
            key.name = KEY_NAME.get(code)

    return key


def parse_multiple_keypresses(
    prev_state: KeyParseState, input_: str | None = ""
) -> tuple[list[ParsedInput], KeyParseState]:
    """Parse multiple keypresses from terminal input."""
    is_flush = input_ is None
    input_string = "" if is_flush else (input_ or "")

    tokenizer = prev_state._tokenizer or create_tokenizer(x10_mouse=True)
    tokens = tokenizer.flush() if is_flush else tokenizer.feed(input_string)

    keys: list[ParsedInput] = []
    in_paste = prev_state.mode == "IN_PASTE"
    paste_buffer = prev_state.paste_buffer

    for token in tokens:
        if token.type == "sequence":
            if token.value == PASTE_START:
                in_paste = True
                paste_buffer = ""
            elif token.value == PASTE_END:
                keys.append(ParsedKey(sequence=paste_buffer, raw=paste_buffer, is_pasted=True))
                in_paste = False
                paste_buffer = ""
            elif in_paste:
                paste_buffer += token.value
            else:
                keys.append(parse_keypress(token.value))
        elif token.type == "text":
            if in_paste:
                paste_buffer += token.value
            else:
                keys.append(parse_keypress(token.value))

    if is_flush and in_paste and paste_buffer:
        keys.append(ParsedKey(sequence=paste_buffer, raw=paste_buffer, is_pasted=True))
        in_paste = False
        paste_buffer = ""

    new_state = KeyParseState(
        mode="IN_PASTE" if in_paste else "NORMAL",
        incomplete=tokenizer.buffer(),
        paste_buffer=paste_buffer,
        _tokenizer=tokenizer,
    )

    return keys, new_state
