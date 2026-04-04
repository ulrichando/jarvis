"""JARVIS Output Styles — configurable response formatting."""

import re
from dataclasses import dataclass, field

VALID_TOOL_DISPLAYS = {"expanded", "compact", "minimal", "hidden"}


@dataclass
class OutputStyle:
    name: str
    markdown_enabled: bool = True
    tool_display: str = "expanded"
    thinking_visible: bool = False
    timestamps: bool = False
    max_length: int = 0  # 0 = unlimited
    strip_markdown: bool = False

    def __post_init__(self):
        if self.tool_display not in VALID_TOOL_DISPLAYS:
            raise ValueError(f"tool_display must be one of {VALID_TOOL_DISPLAYS}")


BUILTIN_STYLES: dict[str, OutputStyle] = {
    "default": OutputStyle(
        name="default",
        markdown_enabled=True,
        tool_display="expanded",
        thinking_visible=False,
    ),
    "minimal": OutputStyle(
        name="minimal",
        markdown_enabled=False,
        tool_display="compact",
        thinking_visible=False,
        strip_markdown=True,
    ),
    "developer": OutputStyle(
        name="developer",
        markdown_enabled=True,
        tool_display="expanded",
        thinking_visible=True,
        timestamps=True,
    ),
    "concise": OutputStyle(
        name="concise",
        markdown_enabled=True,
        tool_display="compact",
        thinking_visible=False,
        max_length=2000,
    ),
}


def get_style(name: str) -> OutputStyle:
    return BUILTIN_STYLES.get(name, BUILTIN_STYLES["default"])


def list_styles() -> list[str]:
    return list(BUILTIN_STYLES.keys())


def apply_style(text: str, style: OutputStyle) -> str:
    if not text:
        return text
    result = text
    if style.strip_markdown:
        result = re.sub(r"#{1,6}\s*", "", result)
        result = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", result)
    if style.max_length and len(result) > style.max_length:
        result = result[:style.max_length] + "\n... (truncated)"
    return result


def format_tool_call(tool_name: str, args: dict, style: OutputStyle) -> str:
    if style.tool_display == "hidden":
        return ""
    if style.tool_display == "minimal":
        return f"[{tool_name}]"
    if style.tool_display == "compact":
        arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{tool_name}({arg_str})"
    # expanded
    lines = [f"Tool: {tool_name}"]
    for k, v in args.items():
        lines.append(f"  {k}: {v!r}")
    return "\n".join(lines)
