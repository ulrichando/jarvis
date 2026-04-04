"""
Width-aware truncation/wrapping utilities.

Uses wcwidth for correct CJK/emoji measurement when available,
falls back to len() otherwise.
"""

from typing import List, Optional
import unicodedata

try:
    from wcwidth import wcswidth, wcwidth as _wcwidth

    def string_width(text: str) -> int:
        w = wcswidth(text)
        return w if w >= 0 else len(text)
except ImportError:
    def string_width(text: str) -> int:
        return len(text)


def _grapheme_segments(text: str) -> List[str]:
    """Simple grapheme segmenter - splits into individual characters.
    For full grapheme cluster support, use the 'grapheme' package."""
    return list(text)


def truncate_path_middle(path: str, max_length: int) -> str:
    """
    Truncates a file path in the middle to preserve both directory context and filename.
    e.g. "src/components/deeply/nested/folder/MyComponent.tsx" becomes
    "src/components/.../MyComponent.tsx" when max_length is 30.
    """
    if string_width(path) <= max_length:
        return path

    if max_length <= 0:
        return "..."

    if max_length < 5:
        return truncate_to_width(path, max_length)

    last_slash = path.rfind("/")
    filename = path[last_slash:] if last_slash >= 0 else path
    directory = path[:last_slash] if last_slash >= 0 else ""
    filename_width = string_width(filename)

    if filename_width >= max_length - 1:
        return truncate_start_to_width(path, max_length)

    available_for_dir = max_length - 1 - filename_width  # -1 for ellipsis

    if available_for_dir <= 0:
        return truncate_start_to_width(filename, max_length)

    truncated_dir = truncate_to_width_no_ellipsis(directory, available_for_dir)
    return truncated_dir + "..." + filename


def truncate_to_width(text: str, max_width: int) -> str:
    """
    Truncates a string to fit within a maximum display width.
    Appends '...' when truncation occurs.
    """
    if string_width(text) <= max_width:
        return text
    if max_width <= 1:
        return "..."

    width = 0
    result = ""
    for segment in _grapheme_segments(text):
        seg_width = string_width(segment)
        if width + seg_width > max_width - 1:
            break
        result += segment
        width += seg_width
    return result + "..."


def truncate_start_to_width(text: str, max_width: int) -> str:
    """
    Truncates from the start of a string, keeping the tail end.
    Prepends '...' when truncation occurs.
    """
    if string_width(text) <= max_width:
        return text
    if max_width <= 1:
        return "..."

    segments = _grapheme_segments(text)
    width = 0
    start_idx = len(segments)
    for i in range(len(segments) - 1, -1, -1):
        seg_width = string_width(segments[i])
        if width + seg_width > max_width - 1:
            break
        width += seg_width
        start_idx = i
    return "..." + "".join(segments[start_idx:])


def truncate_to_width_no_ellipsis(text: str, max_width: int) -> str:
    """
    Truncates a string to fit within a maximum display width, without appending an ellipsis.
    """
    if string_width(text) <= max_width:
        return text
    if max_width <= 0:
        return ""

    width = 0
    result = ""
    for segment in _grapheme_segments(text):
        seg_width = string_width(segment)
        if width + seg_width > max_width:
            break
        result += segment
        width += seg_width
    return result


def truncate(text: str, max_width: int, single_line: bool = False) -> str:
    """
    Truncates a string to fit within a maximum display width.
    Appends '...' when truncation occurs.
    If single_line is True, also truncates at the first newline.
    """
    result = text

    if single_line:
        first_newline = text.find("\n")
        if first_newline != -1:
            result = text[:first_newline]
            if string_width(result) + 1 > max_width:
                return truncate_to_width(result, max_width)
            return f"{result}..."

    if string_width(result) <= max_width:
        return result
    return truncate_to_width(result, max_width)


def wrap_text(text: str, width: int) -> List[str]:
    """Wrap text to a given width, returning a list of lines."""
    lines: List[str] = []
    current_line = ""
    current_width = 0

    for segment in _grapheme_segments(text):
        seg_width = string_width(segment)
        if current_width + seg_width <= width:
            current_line += segment
            current_width += seg_width
        else:
            if current_line:
                lines.append(current_line)
            current_line = segment
            current_width = seg_width

    if current_line:
        lines.append(current_line)
    return lines
