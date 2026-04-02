"""JARVIS configuration — API keys, model settings, paths."""

import logging
import os
from pathlib import Path

log = logging.getLogger("jarvis.config")

# Load .env file if present
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for lineno, line in enumerate(_env_file.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            log.warning(".env:%d: malformed line (no '=' found): %r", lineno, line)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.isidentifier():
            log.warning(".env:%d: invalid key name: %r", lineno, key)
            continue
        os.environ.setdefault(key, value.strip())

# Paths
JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
DATA_DIR = JARVIS_HOME / "data"
EVOLVED_DIR = JARVIS_HOME / "evolved"
LOGS_DIR = JARVIS_HOME / "logs"

# Groq API
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
# llama-3.3-70b-versatile: 128K context, better reasoning, higher TPM on Groq
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Local model (Ollama) — PRIMARY reasoning backend
LOCAL_MODEL = os.environ.get("JARVIS_LOCAL_MODEL", "qwen2.5:7b")
CODE_MODEL = os.environ.get("JARVIS_CODE_MODEL", "deepseek-coder-v2:16b")
LOCAL_MODEL_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Speech — upgraded defaults based on research
# STT: distil-medium.en = better accuracy than small.en at similar speed
# TTS: en_US-lessac-high = better quality than medium at small speed cost
STT_MODEL = os.environ.get("JARVIS_STT_MODEL", "distil-medium.en")
TTS_MODEL = os.environ.get("JARVIS_TTS_MODEL", "en_US-lessac-high")

# Brain settings
MAX_HISTORY = 50
COMMAND_SAFETY_MODE = True


def ensure_dirs():
    """Create necessary directories."""
    for d in [JARVIS_HOME, DATA_DIR, EVOLVED_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
