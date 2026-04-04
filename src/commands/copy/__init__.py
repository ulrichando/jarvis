"""Copy command - Copy Claude's last response to clipboard."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "copy",
    "description": "Copy Claude's last response to clipboard (or /copy N for the Nth-latest)",
}
