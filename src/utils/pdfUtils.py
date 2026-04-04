"""PDF utility functions: page range parsing, extension checking."""

from __future__ import annotations

import math
import re
from typing import Optional

# Document extensions handled specially
DOCUMENT_EXTENSIONS = {"pdf"}


def parse_pdf_page_range(
    pages: str,
) -> Optional[dict[str, int]]:
    """Parse a page range string into firstPage/lastPage numbers.

    Supported formats:
        "5"    -> {"first_page": 5, "last_page": 5}
        "1-10" -> {"first_page": 1, "last_page": 10}
        "3-"   -> {"first_page": 3, "last_page": inf}

    Returns None on invalid input (non-numeric, zero, inverted range).
    Pages are 1-indexed.
    """
    trimmed = pages.strip()
    if not trimmed:
        return None

    # "N-" open-ended range
    if trimmed.endswith("-"):
        try:
            first = int(trimmed[:-1])
        except ValueError:
            return None
        if first < 1:
            return None
        return {"first_page": first, "last_page": math.inf}

    dash_idx = trimmed.find("-")
    if dash_idx == -1:
        # Single page: "5"
        try:
            page = int(trimmed)
        except ValueError:
            return None
        if page < 1:
            return None
        return {"first_page": page, "last_page": page}

    # Range: "1-10"
    try:
        first = int(trimmed[:dash_idx])
        last = int(trimmed[dash_idx + 1 :])
    except ValueError:
        return None
    if first < 1 or last < 1 or last < first:
        return None
    return {"first_page": first, "last_page": last}


def is_pdf_extension(ext: str) -> bool:
    """Check if a file extension is a PDF document."""
    normalized = ext.lstrip(".")
    return normalized.lower() in DOCUMENT_EXTENSIONS
