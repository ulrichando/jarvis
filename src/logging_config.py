"""JARVIS logging configuration."""
import logging
import sys
from pathlib import Path


def setup_logging(level: str = "INFO", log_file: str | None = None, quiet: bool = False):
    """Configure logging for JARVIS.

    Args:
        quiet: If True, only log to file (no stderr). Used by CLI to keep terminal clean.
    """
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    datefmt = "%H:%M:%S"

    handlers = []

    # Only add stderr handler if not in quiet mode
    if not quiet:
        handlers.append(logging.StreamHandler(sys.stderr))

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,  # Override any previous basicConfig
    )

    # Quiet noisy libraries
    for noisy in ["httpx", "httpcore", "numexpr", "numexpr.utils",
                   "anthropic", "anthropic._base_client"]:
        logging.getLogger(noisy).setLevel(logging.ERROR)
