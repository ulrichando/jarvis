"""OSC (Operating System Command) Types and Parser."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from typing import Any, Generator

from .ansi import BEL, ESC, ESC_TYPE, SEP

OSC_PREFIX = ESC + chr(ESC_TYPE.OSC)
ST = ESC + "\\"


def osc(*parts: str | int) -> str:
    """Generate an OSC sequence: ESC ] p1;p2;...;pN <terminator>."""
    # Use BEL terminator by default
    terminator = BEL
    return f"{OSC_PREFIX}{SEP.join(str(p) for p in parts)}{terminator}"


def wrap_for_multiplexer(sequence: str) -> str:
    """Wrap an escape sequence for terminal multiplexer passthrough."""
    if os.environ.get("TMUX"):
        escaped = sequence.replace("\x1b", "\x1b\x1b")
        return f"\x1bPtmux;{escaped}\x1b\\"
    if os.environ.get("STY"):
        return f"\x1bP{sequence}\x1b\\"
    return sequence


ClipboardPath = str  # 'native' | 'tmux-buffer' | 'osc52'


def get_clipboard_path() -> ClipboardPath:
    """Determine which clipboard path will be used."""
    import sys
    native_available = sys.platform == "darwin" and not os.environ.get("SSH_CONNECTION")
    if native_available:
        return "native"
    if os.environ.get("TMUX"):
        return "tmux-buffer"
    return "osc52"


def _tmux_passthrough(payload: str) -> str:
    """Wrap a payload in tmux's DCS passthrough."""
    return f"{ESC}Ptmux;{payload.replace(ESC, ESC + ESC)}{ST}"


async def tmux_load_buffer(text: str) -> bool:
    """Load text into tmux's paste buffer via tmux load-buffer."""
    if not os.environ.get("TMUX"):
        return False
    args = ["tmux", "load-buffer", "-w", "-"]
    if os.environ.get("LC_TERMINAL") == "iTerm2":
        args = ["tmux", "load-buffer", "-"]
    try:
        proc = subprocess.run(
            args, input=text, capture_output=True, text=True, timeout=2
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


async def set_clipboard(text: str) -> str:
    """OSC 52 clipboard write."""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    raw = osc(OSC["CLIPBOARD"], "c", b64)

    if not os.environ.get("SSH_CONNECTION"):
        _copy_native(text)

    tmux_loaded = await tmux_load_buffer(text)

    if tmux_loaded:
        return _tmux_passthrough(f"{ESC}]52;c;{b64}{BEL}")
    return raw


_linux_copy: str | None | object = object()  # sentinel for "not probed"


def _copy_native(text: str) -> None:
    """Shell out to a native clipboard utility."""
    import sys
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["pbcopy"], stdin=subprocess.PIPE, text=True
            ).communicate(input=text, timeout=2)
        elif sys.platform == "linux":
            # Try wl-copy, xclip, xsel in order
            for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
                try:
                    subprocess.Popen(
                        cmd, stdin=subprocess.PIPE, text=True
                    ).communicate(input=text, timeout=2)
                    return
                except FileNotFoundError:
                    continue
    except Exception:
        pass


class OSC:
    """OSC command numbers."""
    SET_TITLE_AND_ICON = 0
    SET_ICON = 1
    SET_TITLE = 2
    SET_COLOR = 4
    SET_CWD = 7
    HYPERLINK = 8
    ITERM2 = 9
    SET_FG_COLOR = 10
    SET_BG_COLOR = 11
    SET_CURSOR_COLOR = 12
    CLIPBOARD = 52
    KITTY = 99
    RESET_COLOR = 104
    RESET_FG_COLOR = 110
    RESET_BG_COLOR = 111
    RESET_CURSOR_COLOR = 112
    SEMANTIC_PROMPT = 133
    GHOSTTY = 777
    TAB_STATUS = 21337

    def __class_getitem__(cls, key: str) -> int:
        return getattr(cls, key)


def parse_osc(content: str) -> dict[str, Any] | None:
    """Parse an OSC sequence into an action."""
    semicolon_idx = content.find(";")
    command = content[:semicolon_idx] if semicolon_idx >= 0 else content
    data = content[semicolon_idx + 1:] if semicolon_idx >= 0 else ""

    try:
        command_num = int(command)
    except ValueError:
        return {"type": "unknown", "sequence": f"\x1b]{content}"}

    if command_num == OSC.SET_TITLE_AND_ICON:
        return {"type": "title", "action": {"type": "both", "title": data}}
    if command_num == OSC.SET_ICON:
        return {"type": "title", "action": {"type": "iconName", "name": data}}
    if command_num == OSC.SET_TITLE:
        return {"type": "title", "action": {"type": "windowTitle", "title": data}}

    if command_num == OSC.HYPERLINK:
        parts = data.split(";", 1)
        params_str = parts[0] if parts else ""
        url = parts[1] if len(parts) > 1 else ""

        if url == "":
            return {"type": "link", "action": {"type": "end"}}

        params: dict[str, str] = {}
        if params_str:
            for pair in params_str.split(":"):
                eq_idx = pair.find("=")
                if eq_idx >= 0:
                    params[pair[:eq_idx]] = pair[eq_idx + 1:]

        return {
            "type": "link",
            "action": {
                "type": "start",
                "url": url,
                "params": params if params else None,
            },
        }

    if command_num == OSC.TAB_STATUS:
        return {"type": "tabStatus", "action": _parse_tab_status(data)}

    return {"type": "unknown", "sequence": f"\x1b]{content}"}


def parse_osc_color(spec: str) -> dict[str, Any] | None:
    """Parse an XParseColor-style color spec into an RGB Color."""
    hex_match = re.match(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$", spec)
    if hex_match:
        return {
            "type": "rgb",
            "r": int(hex_match.group(1), 16),
            "g": int(hex_match.group(2), 16),
            "b": int(hex_match.group(3), 16),
        }

    rgb_match = re.match(
        r"^rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})$", spec
    )
    if rgb_match:
        def scale(s: str) -> int:
            return round((int(s, 16) / (16 ** len(s) - 1)) * 255)
        return {
            "type": "rgb",
            "r": scale(rgb_match.group(1)),
            "g": scale(rgb_match.group(2)),
            "b": scale(rgb_match.group(3)),
        }
    return None


def _parse_tab_status(data: str) -> dict[str, Any]:
    """Parse OSC 21337 payload."""
    action: dict[str, Any] = {}
    for key, value in _split_tab_status_pairs(data):
        if key == "indicator":
            action["indicator"] = None if value == "" else parse_osc_color(value)
        elif key == "status":
            action["status"] = None if value == "" else value
        elif key == "status-color":
            action["statusColor"] = None if value == "" else parse_osc_color(value)
    return action


def _split_tab_status_pairs(data: str) -> Generator[tuple[str, str], None, None]:
    """Split k=v;k=v honoring backslash escapes."""
    key = ""
    val = ""
    in_val = False
    esc = False
    for c in data:
        if esc:
            if in_val:
                val += c
            else:
                key += c
            esc = False
        elif c == "\\":
            esc = True
        elif c == ";":
            yield key, val
            key = ""
            val = ""
            in_val = False
        elif c == "=" and not in_val:
            in_val = True
        elif in_val:
            val += c
        else:
            key += c
    if key or in_val:
        yield key, val


def _osc8_id(url: str) -> str:
    """Generate a hash-based ID for OSC 8 hyperlinks."""
    h = 0
    for ch in url:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    return format(h, "x")


def link(url: str, params: dict[str, str] | None = None) -> str:
    """Start a hyperlink (OSC 8)."""
    if not url:
        return LINK_END
    p = {"id": _osc8_id(url)}
    if params:
        p.update(params)
    param_str = ":".join(f"{k}={v}" for k, v in p.items())
    return osc(OSC.HYPERLINK, param_str, url)


LINK_END = osc(OSC.HYPERLINK, "", "")

# iTerm2 OSC 9 subcommands
class ITERM2:
    NOTIFY = 0
    BADGE = 2
    PROGRESS = 4


class PROGRESS:
    CLEAR = 0
    SET = 1
    ERROR = 2
    INDETERMINATE = 3


CLEAR_ITERM2_PROGRESS = f"{OSC_PREFIX}{OSC.ITERM2};{ITERM2.PROGRESS};{PROGRESS.CLEAR};{BEL}"
CLEAR_TERMINAL_TITLE = f"{OSC_PREFIX}{OSC.SET_TITLE_AND_ICON};{BEL}"
CLEAR_TAB_STATUS = osc(OSC.TAB_STATUS, "indicator=;status=;status-color=")


def supports_tab_status() -> bool:
    return os.environ.get("USER_TYPE") == "ant"


def tab_status(fields: dict[str, Any]) -> str:
    """Emit an OSC 21337 tab-status sequence."""
    parts: list[str] = []

    def rgb(c: dict) -> str:
        if c.get("type") == "rgb":
            return "#{:02x}{:02x}{:02x}".format(c["r"], c["g"], c["b"])
        return ""

    if "indicator" in fields:
        parts.append(f"indicator={rgb(fields['indicator']) if fields['indicator'] else ''}")
    if "status" in fields:
        status = fields["status"]
        if status:
            status = status.replace("\\", "\\\\").replace(";", "\\;")
        else:
            status = ""
        parts.append(f"status={status}")
    if "statusColor" in fields:
        parts.append(
            f"status-color={rgb(fields['statusColor']) if fields['statusColor'] else ''}"
        )
    return osc(OSC.TAB_STATUS, ";".join(parts))
def wrapForMultiplexer(*args, **kwargs): return args[0] if args else None
