"""
Sync file-read utilities.

Provides file reading with encoding detection and line ending detection,
extracted as a leaf module with minimal dependencies.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

LineEndingType = Literal["CRLF", "LF"]


def detect_encoding_for_resolved_path(resolved_path: str) -> str:
    """
    Detect the encoding of a file by reading its BOM header.

    Args:
        resolved_path: The resolved (real) path to the file.

    Returns:
        The detected encoding string ('utf-8', 'utf-16-le', etc.).
    """
    try:
        with open(resolved_path, "rb") as f:
            header = f.read(4096)
    except OSError:
        return "utf-8"

    if len(header) == 0:
        return "utf-8"

    # Check for UTF-16 LE BOM
    if len(header) >= 2 and header[0] == 0xFF and header[1] == 0xFE:
        return "utf-16-le"

    # Check for UTF-8 BOM
    if len(header) >= 3 and header[0] == 0xEF and header[1] == 0xBB and header[2] == 0xBF:
        return "utf-8"

    # Default to utf-8
    return "utf-8"


def detect_line_endings_for_string(content: str) -> LineEndingType:
    """
    Detect the dominant line ending style in a string.

    Args:
        content: The text content to analyze.

    Returns:
        'CRLF' if Windows-style line endings dominate, 'LF' otherwise.
    """
    crlf_count = 0
    lf_count = 0

    for i, ch in enumerate(content):
        if ch == "\n":
            if i > 0 and content[i - 1] == "\r":
                crlf_count += 1
            else:
                lf_count += 1

    return "CRLF" if crlf_count > lf_count else "LF"


def read_file_sync_with_metadata(file_path: str) -> dict:
    """
    Read a file and return content with encoding and line ending metadata.

    Returns a dict with keys: content, encoding, line_endings.
    The content has CRLF normalized to LF.
    """
    encoding = detect_encoding_for_resolved_path(file_path)

    with open(file_path, "r", encoding=encoding) as f:
        raw = f.read()

    # Detect line endings from raw content before normalization
    sample = raw[:4096]
    line_endings = detect_line_endings_for_string(sample)

    # Normalize CRLF to LF
    content = raw.replace("\r\n", "\n")

    return {
        "content": content,
        "encoding": encoding,
        "line_endings": line_endings,
    }


def read_file_sync(file_path: str) -> str:
    """Read a file and return its content with CRLF normalized to LF."""
    return read_file_sync_with_metadata(file_path)["content"]
