"""
Pure display formatters for file sizes, durations, numbers, and relative times.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


def format_file_size(size_in_bytes: int) -> str:
    """Formats a byte count to a human-readable string (KB, MB, GB)."""
    kb = size_in_bytes / 1024
    if kb < 1:
        return f"{size_in_bytes} bytes"
    if kb < 1024:
        val = f"{kb:.1f}".rstrip("0").rstrip(".")
        return f"{val}KB"
    mb = kb / 1024
    if mb < 1024:
        val = f"{mb:.1f}".rstrip("0").rstrip(".")
        return f"{val}MB"
    gb = mb / 1024
    val = f"{gb:.1f}".rstrip("0").rstrip(".")
    return f"{val}GB"


def format_seconds_short(ms: float) -> str:
    """Formats milliseconds as seconds with 1 decimal place."""
    return f"{ms / 1000:.1f}s"


def format_duration(
    ms: float,
    hide_trailing_zeros: bool = False,
    most_significant_only: bool = False,
) -> str:
    """Format a duration in milliseconds to a human-readable string."""
    if ms < 60000:
        if ms == 0:
            return "0s"
        if ms < 1:
            return f"{ms / 1000:.1f}s"
        s = int(ms // 1000)
        return f"{s}s"

    days = int(ms // 86400000)
    hours = int((ms % 86400000) // 3600000)
    minutes = int((ms % 3600000) // 60000)
    seconds = round((ms % 60000) / 1000)

    if seconds == 60:
        seconds = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        hours += 1
    if hours == 24:
        hours = 0
        days += 1

    if most_significant_only:
        if days > 0:
            return f"{days}d"
        if hours > 0:
            return f"{hours}h"
        if minutes > 0:
            return f"{minutes}m"
        return f"{seconds}s"

    hide = hide_trailing_zeros

    if days > 0:
        if hide and hours == 0 and minutes == 0:
            return f"{days}d"
        if hide and minutes == 0:
            return f"{days}d {hours}h"
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        if hide and minutes == 0 and seconds == 0:
            return f"{hours}h"
        if hide and seconds == 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        if hide and seconds == 0:
            return f"{minutes}m"
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_number(number: int | float) -> str:
    """Format a number with compact notation (e.g., 1.3k)."""
    if abs(number) >= 1_000_000_000:
        val = number / 1_000_000_000
        return f"{val:.1f}b"
    if abs(number) >= 1_000_000:
        val = number / 1_000_000
        return f"{val:.1f}m"
    if abs(number) >= 1000:
        val = number / 1000
        return f"{val:.1f}k"
    return str(int(number))


def format_tokens(count: int) -> str:
    """Format a token count with compact notation."""
    return format_number(count).replace(".0", "")


def format_relative_time(
    date: datetime,
    now: Optional[datetime] = None,
    style: str = "narrow",
) -> str:
    """Format a relative time string."""
    if now is None:
        now = datetime.now(tz=date.tzinfo)

    diff = date - now
    diff_seconds = int(diff.total_seconds())

    intervals = [
        ("y", 31536000),
        ("mo", 2592000),
        ("w", 604800),
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
        ("s", 1),
    ]

    for unit, seconds in intervals:
        if abs(diff_seconds) >= seconds:
            value = int(diff_seconds / seconds)
            if diff_seconds < 0:
                return f"{abs(value)}{unit} ago"
            return f"in {value}{unit}"

    return "0s ago" if diff_seconds <= 0 else "in 0s"


def format_relative_time_ago(
    date: datetime,
    now: Optional[datetime] = None,
    style: str = "narrow",
) -> str:
    """Format a relative time string, ensuring past dates show 'ago'."""
    if now is None:
        now = datetime.now(tz=date.tzinfo)
    return format_relative_time(date, now=now, style=style)
