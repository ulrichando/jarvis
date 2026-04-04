"""
XML/HTML escaping utilities.
"""

from __future__ import annotations


def escape_xml(s: str) -> str:
    """Escape XML/HTML special characters for safe interpolation into element text."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_xml_attr(s: str) -> str:
    """Escape for interpolation into a double- or single-quoted attribute value."""
    return escape_xml(s).replace('"', "&quot;").replace("'", "&apos;")
