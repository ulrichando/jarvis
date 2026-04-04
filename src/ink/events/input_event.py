"""Input event with parsed key information."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .event import Event

# Non-alphanumeric key names that should clear input
NON_ALPHANUMERIC_KEYS = [
    "up", "down", "left", "right",
    "pagedown", "pageup",
    "wheelup", "wheeldown",
    "home", "end",
    "return", "escape",
    "tab", "backspace", "delete",
    "insert", "clear",
    "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f11", "f12",
]


@dataclass
class Key:
    """Parsed key state."""
    up_arrow: bool = False
    down_arrow: bool = False
    left_arrow: bool = False
    right_arrow: bool = False
    page_down: bool = False
    page_up: bool = False
    wheel_up: bool = False
    wheel_down: bool = False
    home: bool = False
    end: bool = False
    return_: bool = False
    escape: bool = False
    ctrl: bool = False
    shift: bool = False
    fn: bool = False
    tab: bool = False
    backspace: bool = False
    delete: bool = False
    meta: bool = False
    super_: bool = False


@dataclass
class ParsedKey:
    """Parsed keypress from terminal input."""
    name: str = ""
    sequence: str = ""
    ctrl: bool = False
    shift: bool = False
    meta: bool = False
    option: bool = False
    fn: bool = False
    super_: bool = False
    code: str = ""


def parse_key(keypress: ParsedKey) -> tuple[Key, str]:
    """Parse a keypress into a Key and input string."""
    key = Key(
        up_arrow=keypress.name == "up",
        down_arrow=keypress.name == "down",
        left_arrow=keypress.name == "left",
        right_arrow=keypress.name == "right",
        page_down=keypress.name == "pagedown",
        page_up=keypress.name == "pageup",
        wheel_up=keypress.name == "wheelup",
        wheel_down=keypress.name == "wheeldown",
        home=keypress.name == "home",
        end=keypress.name == "end",
        return_=keypress.name == "return",
        escape=keypress.name == "escape",
        fn=keypress.fn,
        ctrl=keypress.ctrl,
        shift=keypress.shift,
        tab=keypress.name == "tab",
        backspace=keypress.name == "backspace",
        delete=keypress.name == "delete",
        meta=keypress.meta or keypress.name == "escape" or keypress.option,
        super_=keypress.super_,
    )

    input_ = keypress.name if keypress.ctrl else keypress.sequence

    if input_ is None:
        input_ = ""

    # When ctrl is set, keypress.name for space is the literal word "space"
    if keypress.ctrl and input_ == "space":
        input_ = " "

    # Suppress unrecognized escape sequences parsed as function keys
    if keypress.code and not keypress.name:
        input_ = ""

    # Strip meta if still remaining
    if input_.startswith("\x1b"):
        input_ = input_[1:]

    processed_as_special = False

    # Handle CSI u sequences (Kitty keyboard protocol)
    import re
    if re.match(r"^\[\d", input_) and input_.endswith("u"):
        if not keypress.name:
            input_ = ""
        else:
            if keypress.name == "space":
                input_ = " "
            elif keypress.name == "escape":
                input_ = ""
            else:
                input_ = keypress.name
        processed_as_special = True

    # Handle xterm modifyOtherKeys sequences
    if input_.startswith("[27;") and input_.endswith("~"):
        if not keypress.name:
            input_ = ""
        else:
            if keypress.name == "space":
                input_ = " "
            elif keypress.name == "escape":
                input_ = ""
            else:
                input_ = keypress.name
        processed_as_special = True

    # Handle application keypad mode sequences
    if (
        input_.startswith("O")
        and len(input_) == 2
        and keypress.name
        and len(keypress.name) == 1
    ):
        input_ = keypress.name
        processed_as_special = True

    # Clear input for non-alphanumeric keys
    if (
        not processed_as_special
        and keypress.name
        and keypress.name in NON_ALPHANUMERIC_KEYS
    ):
        input_ = ""

    # Set shift=True for uppercase letters (A-Z)
    if len(input_) == 1 and "A" <= input_[0] <= "Z":
        key.shift = True

    return key, input_


class InputEvent(Event):
    """Input event with parsed key information."""

    def __init__(self, keypress: ParsedKey) -> None:
        super().__init__()
        key, input_ = parse_key(keypress)
        self.keypress: ParsedKey = keypress
        self.key: Key = key
        self.input: str = input_
