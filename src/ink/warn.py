"""Validation warnings for non-integer values."""

import logging

logger = logging.getLogger(__name__)


def if_not_integer(value: int | None, name: str) -> None:
    """Warn if value is not an integer."""
    if value is None:
        return
    if isinstance(value, int):
        return
    logger.warning("%s should be an integer, got %s", name, value)
