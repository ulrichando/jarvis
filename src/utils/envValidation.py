"""
Environment variable validation utilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)

Status = Literal["valid", "capped", "invalid"]


@dataclass
class EnvVarValidationResult:
    """Result of validating a bounded integer environment variable."""

    effective: int
    status: Status
    message: Optional[str] = None


def validate_bounded_int_env_var(
    name: str,
    value: Optional[str],
    default_value: int,
    upper_limit: int,
) -> EnvVarValidationResult:
    """
    Validate an environment variable as a bounded positive integer.

    Args:
        name: The environment variable name (for logging).
        value: The raw string value (or None if not set).
        default_value: Value to use when not set or invalid.
        upper_limit: Maximum allowed value; values above this are capped.

    Returns:
        EnvVarValidationResult with the effective value and status.
    """
    if not value:
        return EnvVarValidationResult(effective=default_value, status="valid")

    try:
        parsed = int(value)
    except ValueError:
        msg = f'Invalid value "{value}" (using default: {default_value})'
        logger.debug(f"{name} {msg}")
        return EnvVarValidationResult(
            effective=default_value, status="invalid", message=msg
        )

    if parsed <= 0:
        msg = f'Invalid value "{value}" (using default: {default_value})'
        logger.debug(f"{name} {msg}")
        return EnvVarValidationResult(
            effective=default_value, status="invalid", message=msg
        )

    if parsed > upper_limit:
        msg = f"Capped from {parsed} to {upper_limit}"
        logger.debug(f"{name} {msg}")
        return EnvVarValidationResult(
            effective=upper_limit, status="capped", message=msg
        )

    return EnvVarValidationResult(effective=parsed, status="valid")
