"""Minimal cron expression parsing and next-run calculation.

Supports 5-field cron: minute hour day-of-month month day-of-week
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class CronFields:
    minute: list[int]
    hour: list[int]
    day_of_month: list[int]
    month: list[int]
    day_of_week: list[int]


FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0=Sunday)
]


def _expand_field(field: str, min_val: int, max_val: int) -> Optional[list[int]]:
    """Parse a single cron field into a sorted array of matching values."""
    values: set[int] = set()

    for part in field.split(","):
        part = part.strip()

        # Wildcard or step
        if part.startswith("*"):
            step = 1
            if "/" in part:
                try:
                    step = int(part.split("/")[1])
                except (ValueError, IndexError):
                    return None
            if step < 1:
                return None
            for i in range(min_val, max_val + 1, step):
                values.add(i)
            continue

        # Range
        if "-" in part:
            range_parts = part.split("-")
            try:
                lo = int(range_parts[0])
                hi_str = range_parts[1]
                step = 1
                if "/" in hi_str:
                    hi_str, step_str = hi_str.split("/")
                    step = int(step_str)
                hi = int(hi_str)
            except (ValueError, IndexError):
                return None
            if lo > hi or step < 1:
                return None
            for i in range(lo, hi + 1, step):
                if min_val == 0 and max_val == 6 and i == 7:
                    values.add(0)
                else:
                    values.add(i)
            continue

        # Plain number
        try:
            n = int(part)
            if min_val == 0 and max_val == 6 and n == 7:
                n = 0
            if n < min_val or n > max_val:
                return None
            values.add(n)
        except ValueError:
            return None

    if not values:
        return None
    return sorted(values)


def parse_cron_expression(expr: str) -> Optional[CronFields]:
    """Parse a 5-field cron expression."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return None

    fields = []
    for i, part in enumerate(parts):
        min_val, max_val = FIELD_RANGES[i]
        expanded = _expand_field(part, min_val, max_val)
        if expanded is None:
            return None
        fields.append(expanded)

    return CronFields(
        minute=fields[0],
        hour=fields[1],
        day_of_month=fields[2],
        month=fields[3],
        day_of_week=fields[4],
    )


def compute_next_cron_run(
    fields: CronFields, after: Optional[datetime] = None
) -> Optional[datetime]:
    """Compute the next run time for a cron expression."""
    if after is None:
        after = datetime.now()

    # Start from the next minute
    current = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Search up to 1 year ahead
    max_time = after + timedelta(days=366)

    while current < max_time:
        if (
            current.minute in fields.minute
            and current.hour in fields.hour
            and current.day in fields.day_of_month
            and current.month in fields.month
            and current.weekday() in _py_weekday_to_cron(fields.day_of_week)
        ):
            return current
        current += timedelta(minutes=1)

    return None


def _py_weekday_to_cron(cron_days: list[int]) -> set[int]:
    """Convert cron day-of-week (0=Sun) to Python weekday (0=Mon)."""
    mapping = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
    return {mapping[d] for d in cron_days}


def cron_to_human(expr: str) -> str:
    """Convert a cron expression to human-readable text."""
    fields = parse_cron_expression(expr)
    if fields is None:
        return expr
    return f"cron({expr})"
