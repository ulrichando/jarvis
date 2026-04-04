"""Tip display history tracking."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

_tips_history: Dict[str, int] = {}
_num_startups: int = 0


def _load_config() -> dict:
    config_path = Path(os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))) / "config.json"
    try:
        if config_path.exists():
            return json.loads(config_path.read_text())
    except Exception:
        pass
    return {}


def record_tip_shown(tip_id: str) -> None:
    """Record that a tip was shown in the current session."""
    config = _load_config()
    num_startups = config.get("numStartups", 0)
    history = config.get("tipsHistory", {})
    if history.get(tip_id) == num_startups:
        return
    history[tip_id] = num_startups
    config["tipsHistory"] = history
    config_path = Path(os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))) / "config.json"
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2))
    except Exception:
        pass


def get_sessions_since_last_shown(tip_id: str) -> float:
    """Get the number of sessions since a tip was last shown."""
    config = _load_config()
    last_shown = config.get("tipsHistory", {}).get(tip_id)
    if last_shown is None:
        return float("inf")
    return config.get("numStartups", 0) - last_shown
