"""
Leaf JSON read utility.

Provides UTF-8 BOM stripping for JSON content, extracted to avoid
import cycles in the settings chain.
"""

from __future__ import annotations

UTF8_BOM = "\ufeff"


def strip_bom(content: str) -> str:
    """
    Strip UTF-8 BOM (U+FEFF) from the beginning of a string.

    PowerShell 5.x writes UTF-8 with BOM by default. Without stripping,
    json.loads() fails with "Unexpected character".
    """
    return content[1:] if content.startswith(UTF8_BOM) else content
