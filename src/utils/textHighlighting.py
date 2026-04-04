"""
Text highlighting utilities for segmenting text by highlight ranges.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TextHighlight:
    start: int
    end: int
    color: Optional[str] = None
    dim_color: bool = False
    inverse: bool = False
    shimmer_color: Optional[str] = None
    priority: int = 0


@dataclass
class TextSegment:
    text: str
    start: int
    highlight: Optional[TextHighlight] = None


def segment_text_by_highlights(
    text: str,
    highlights: List[TextHighlight],
) -> List[TextSegment]:
    """
    Segment text by highlight ranges, resolving overlaps by priority.

    Args:
        text: The text to segment.
        highlights: List of highlights with start/end positions and priority.

    Returns:
        List of TextSegments with optional highlight info.
    """
    if not highlights:
        return [TextSegment(text=text, start=0)]

    # Sort by start position, then by descending priority
    sorted_highlights = sorted(
        highlights,
        key=lambda h: (h.start, -h.priority),
    )

    # Resolve overlaps: higher priority wins
    resolved: List[TextHighlight] = []
    used_ranges: List[Dict[str, int]] = []

    for highlight in sorted_highlights:
        if highlight.start == highlight.end:
            continue

        overlaps = any(
            (highlight.start >= r["start"] and highlight.start < r["end"])
            or (highlight.end > r["start"] and highlight.end <= r["end"])
            or (highlight.start <= r["start"] and highlight.end >= r["end"])
            for r in used_ranges
        )

        if not overlaps:
            resolved.append(highlight)
            used_ranges.append({"start": highlight.start, "end": highlight.end})

    if not resolved:
        return [TextSegment(text=text, start=0)]

    # Build segments
    segments: List[TextSegment] = []
    pos = 0

    for highlight in sorted(resolved, key=lambda h: h.start):
        # Add unhighlighted segment before this highlight
        if highlight.start > pos:
            segments.append(TextSegment(
                text=text[pos:highlight.start],
                start=pos,
            ))

        # Add highlighted segment
        segments.append(TextSegment(
            text=text[highlight.start:highlight.end],
            start=highlight.start,
            highlight=highlight,
        ))
        pos = highlight.end

    # Add remaining text after last highlight
    if pos < len(text):
        segments.append(TextSegment(
            text=text[pos:],
            start=pos,
        ))

    return segments
