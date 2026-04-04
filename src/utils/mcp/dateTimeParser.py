"""Natural language date/time parsing to ISO 8601 format."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Union


@dataclass
class DateTimeParseSuccess:
    success: bool = True
    value: str = ""


@dataclass
class DateTimeParseFailure:
    success: bool = False
    error: str = ""


DateTimeParseResult = Union[DateTimeParseSuccess, DateTimeParseFailure]


async def parse_natural_language_date_time(
    input_text: str,
    format_: Literal["date", "date-time"],
) -> DateTimeParseResult:
    """Parse natural language date/time input into ISO 8601 format.

    Examples:
        "tomorrow at 3pm" -> "2025-10-15T15:00:00-07:00"
        "next Monday"     -> "2025-10-20"
        "in 2 hours"      -> "2025-10-14T12:30:00-07:00"

    This is a simplified version that attempts common patterns.
    The TypeScript original uses an LLM (Haiku) for parsing.
    """
    try:
        from dateutil import parser as date_parser

        parsed = date_parser.parse(input_text, fuzzy=True)

        if format_ == "date":
            return DateTimeParseSuccess(value=parsed.strftime("%Y-%m-%d"))
        else:
            return DateTimeParseSuccess(value=parsed.isoformat())
    except Exception:
        pass

    # Try basic ISO format
    if looks_like_iso8601(input_text):
        return DateTimeParseSuccess(value=input_text.strip())

    return DateTimeParseFailure(error="Unable to parse date/time from input")


def looks_like_iso8601(input_text: str) -> bool:
    """Check if a string looks like it might be an ISO 8601 date/time."""
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}(T|$)", input_text.strip()))
