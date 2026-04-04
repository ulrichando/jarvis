"""
Internationalization utilities with lazy initialization.

Provides cached text segmentation and locale detection functions.
"""

from __future__ import annotations

import locale
import time
from functools import lru_cache
from typing import Optional

try:
    import grapheme as _grapheme_module

    _HAS_GRAPHEME = True
except ImportError:
    _HAS_GRAPHEME = False


def first_grapheme(text: str) -> str:
    """
    Extract the first grapheme cluster from a string.
    Returns '' for empty strings.
    """
    if not text:
        return ""
    if _HAS_GRAPHEME:
        for g in _grapheme_module.graphemes(text):
            return g
    return text[0]


def last_grapheme(text: str) -> str:
    """
    Extract the last grapheme cluster from a string.
    Returns '' for empty strings.
    """
    if not text:
        return ""
    if _HAS_GRAPHEME:
        clusters = list(_grapheme_module.graphemes(text))
        return clusters[-1] if clusters else ""
    return text[-1]


@lru_cache(maxsize=1)
def get_time_zone() -> str:
    """Get the system timezone. Cached for the process lifetime."""
    try:
        return time.tzname[0] or "UTC"
    except (IndexError, AttributeError):
        return "UTC"


@lru_cache(maxsize=1)
def get_system_locale_language() -> Optional[str]:
    """
    Get the system locale language subtag (e.g. 'en', 'ja').
    Cached for the process lifetime.
    Returns None if unavailable.
    """
    try:
        lang, _ = locale.getdefaultlocale()
        if lang:
            # Extract language subtag (e.g. 'en' from 'en_US')
            return lang.split("_")[0]
    except Exception:
        pass
    return None
