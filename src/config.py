"""JARVIS configuration — paths, directories, defaults.

All user data lives under ~/.jarvis/ (override with JARVIS_HOME env var).
"""

import os
import logging
from pathlib import Path

# ── Core paths ────────────────────────────────────────────────────────
JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis")))
DATA_DIR = JARVIS_HOME / "data"
EVOLVED_DIR = JARVIS_HOME / "evolved"

# ── Model defaults ────────────────────────────────────────────────────
STT_MODEL = os.environ.get("JARVIS_STT_MODEL", "base")
TTS_MODEL = os.environ.get("JARVIS_TTS_MODEL", "en-US-GuyNeural")

# ── Memory defaults ───────────────────────────────────────────────────
MAX_HISTORY = int(os.environ.get("JARVIS_MAX_HISTORY", "100"))

# ── Logging ───────────────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("JARVIS_LOG_LEVEL", "INFO")
LOG_FILE = JARVIS_HOME / "jarvis.log"


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    for d in [
        JARVIS_HOME,
        DATA_DIR,
        EVOLVED_DIR,
        JARVIS_HOME / "plugins",
        JARVIS_HOME / "skills",
        JARVIS_HOME / "agents",
        JARVIS_HOME / "sessions",
        JARVIS_HOME / "memory",
    ]:
        d.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    """Configure root logger for JARVIS (delegates to logging_config)."""
    from src.logging_config import setup_logging as _setup
    _setup(level=LOG_LEVEL, log_file=str(LOG_FILE))
    return logging.getLogger("jarvis")
