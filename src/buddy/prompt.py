"""Companion intro text and attachment generation."""

from __future__ import annotations

from typing import Any, Optional

from .companion import get_companion


def companion_intro_text(name: str, species: str) -> str:
    """Generate the companion intro system prompt text."""
    return f"""# Companion

A small {species} named {name} sits beside the user's input box and occasionally comments in a speech bubble. You're not {name} -- it's a separate watcher.

When the user addresses {name} directly (by name), its bubble will answer. Your job in that moment is to stay out of the way: respond in ONE line or less, or just answer any part of the message meant for you. Don't explain that you're not {name} -- they know. Don't narrate what {name} might say -- the bubble handles that."""


def get_companion_intro_attachment(
    messages: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Get companion intro attachments if needed."""
    companion = get_companion()
    if not companion:
        return []

    # Skip if already announced for this companion
    for msg in messages or []:
        if msg.get("type") != "attachment":
            continue
        attachment = msg.get("attachment", {})
        if attachment.get("type") != "companion_intro":
            continue
        if attachment.get("name") == companion.name:
            return []

    return [
        {
            "type": "companion_intro",
            "name": companion.name,
            "species": companion.species,
        }
    ]
