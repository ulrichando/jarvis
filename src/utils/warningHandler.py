"""
Warning handler for capturing and logging Python warnings.
"""

import logging
import os
import re
import warnings
from typing import Dict, Optional

logger = logging.getLogger(__name__)

MAX_WARNING_KEYS = 1000
_warning_counts: Dict[str, int] = {}
_handler_installed = False

# Patterns for internal warnings we want to suppress
INTERNAL_WARNING_PATTERNS = [
    re.compile(r"MaxListenersExceededWarning.*AbortSignal"),
    re.compile(r"MaxListenersExceededWarning.*EventTarget"),
]


def _is_internal_warning(message: str) -> bool:
    """Check if a warning is a known internal warning."""
    return any(pattern.search(message) for pattern in INTERNAL_WARNING_PATTERNS)


def _warning_handler(
    message: Warning,
    category: type,
    filename: str,
    lineno: int,
    file=None,
    line=None,
) -> None:
    """Custom warning handler."""
    try:
        warning_str = f"{category.__name__}: {message}"
        warning_key = f"{category.__name__}: {str(message)[:50]}"

        count = _warning_counts.get(warning_key, 0)

        if warning_key in _warning_counts or len(_warning_counts) < MAX_WARNING_KEYS:
            _warning_counts[warning_key] = count + 1

        is_internal = _is_internal_warning(warning_str)

        # In debug mode, show all warnings
        debug = os.environ.get("CLAUDE_DEBUG", "").lower() in ("1", "true", "yes")
        if debug:
            prefix = "[Internal Warning]" if is_internal else "[Warning]"
            logger.warning(f"{prefix} {warning_str}")

    except Exception:
        pass  # Fail silently


def reset_warning_handler() -> None:
    """Reset the warning handler state (for testing)."""
    global _handler_installed
    _handler_installed = False
    _warning_counts.clear()
    warnings.resetwarnings()


def initialize_warning_handler() -> None:
    """Initialize the warning handler."""
    global _handler_installed

    if _handler_installed:
        return

    # For production, suppress default warning output
    is_development = os.environ.get("NODE_ENV") == "development"
    if not is_development:
        warnings.filterwarnings("ignore")

    warnings.showwarning = _warning_handler
    _handler_installed = True
