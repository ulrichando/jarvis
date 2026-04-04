"""
Release notes parsing and display utilities.

Parses a markdown changelog into structured release notes and supports
filtering by version to show recent changes.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

MAX_RELEASE_NOTES_SHOWN = 5


def parse_changelog(content: str) -> Dict[str, List[str]]:
    """
    Parse a changelog string in markdown format into a structured format.

    Args:
        content: The changelog content string (markdown with ## version headings).

    Returns:
        Dict mapping version numbers to lists of release notes.
    """
    if not content:
        return {}

    release_notes: Dict[str, List[str]] = {}

    # Split by heading lines (## X.X.X)
    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]

    for section in sections:
        lines = section.strip().split("\n")
        if not lines:
            continue

        version_line = lines[0]
        if not version_line:
            continue

        # First part before any dash is the version
        version = version_line.split(" - ")[0].strip()
        if not version:
            continue

        # Extract bullet points
        notes = []
        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith("- "):
                note = stripped[2:].strip()
                if note:
                    notes.append(note)

        if notes:
            release_notes[version] = notes

    return release_notes


def _version_tuple(version: str) -> Tuple[int, ...]:
    """Parse a version string into a comparable tuple of integers."""
    # Strip any leading 'v'
    v = version.lstrip("v")
    parts = []
    for part in v.split("."):
        # Extract leading digits
        m = re.match(r"(\d+)", part)
        if m:
            parts.append(int(m.group(1)))
        else:
            parts.append(0)
    return tuple(parts)


def _version_gt(a: str, b: str) -> bool:
    """Return True if version a is greater than version b."""
    return _version_tuple(a) > _version_tuple(b)


def get_recent_release_notes(
    current_version: str,
    previous_version: Optional[str],
    changelog_content: str = "",
) -> List[str]:
    """
    Get release notes to show based on the previously seen version.
    Shows up to MAX_RELEASE_NOTES_SHOWN items total, prioritizing most recent versions.

    Args:
        current_version: The current app version.
        previous_version: The last version where release notes were seen (or None).
        changelog_content: The raw changelog markdown content.

    Returns:
        List of release note strings to display.
    """
    try:
        release_notes = parse_changelog(changelog_content)

        if not previous_version or _version_gt(current_version, previous_version):
            entries = [
                (ver, notes)
                for ver, notes in release_notes.items()
                if not previous_version or _version_gt(ver, previous_version)
            ]
            entries.sort(key=lambda x: _version_tuple(x[0]), reverse=True)
            result = []
            for _, notes in entries:
                result.extend(notes)
            return [n for n in result if n][:MAX_RELEASE_NOTES_SHOWN]
    except Exception:
        return []

    return []


def get_all_release_notes(
    changelog_content: str = "",
) -> List[Tuple[str, List[str]]]:
    """
    Get all release notes as a list of (version, notes) tuples.
    Versions are sorted with oldest first.

    Args:
        changelog_content: The raw changelog markdown content.

    Returns:
        List of (version, notes_list) tuples.
    """
    try:
        release_notes = parse_changelog(changelog_content)

        sorted_versions = sorted(release_notes.keys(), key=_version_tuple)

        result = []
        for version in sorted_versions:
            notes = [n for n in release_notes[version] if n]
            if notes:
                result.append((version, notes))
        return result
    except Exception:
        return []
