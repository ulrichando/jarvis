"""Persistence for Cortical Vision learned data.

Saves/loads face identities, object libraries, and scene baselines
to ~/.jarvis/cortex/ as JSON with base64-encoded numpy arrays.
"""

import json
import base64
import numpy as np
from pathlib import Path

CORTEX_DIR = Path.home() / ".jarvis" / "cortex"


def ensure_dir():
    CORTEX_DIR.mkdir(parents=True, exist_ok=True)


def encode_array(arr: np.ndarray) -> str:
    return base64.b64encode(arr.astype(np.float32).tobytes()).decode('ascii')


def decode_array(s: str, size: int) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.float32)[:size].copy()


def save_json(filename: str, data: dict):
    ensure_dir()
    path = CORTEX_DIR / filename
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(path)


def load_json(filename: str) -> dict | None:
    path = CORTEX_DIR / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None
