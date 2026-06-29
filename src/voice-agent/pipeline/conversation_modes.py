"""Conversation modes — named presets that bundle the voice/CLI model, TTS
voice, on-device toggle, and a tool allowlist. Selecting a mode writes the
existing ~/.jarvis single-setting files as a set (see the
2026-06-29-conversation-modes-design.md spec). Lock-protected atomic writes,
mirroring pipeline/file_memory.py."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.modes")

MODES_FILE: Path = Path.home() / ".jarvis" / "modes.json"
_LOCK = threading.Lock()

_BUILTINS: list[dict[str, Any]] = [
    {
        "id": "deepseek", "label": "DeepSeek", "voice_mode": "cloud",
        "voice_model": "deepseek-v4-flash", "cli_model": "deepseek-v4-pro",
        "tts_provider": "kokoro:af_bella", "tts_voice": "af_bella",
        "allowed_tools": None,
    },
    {
        "id": "claude", "label": "Claude", "voice_mode": "cloud",
        "voice_model": "claude-haiku-4-5", "cli_model": "claude-sonnet-4-6",
        "tts_provider": "kokoro:af_bella", "tts_voice": "af_bella",
        "allowed_tools": None,
    },
    {
        "id": "local", "label": "Local (on-device)", "voice_mode": "local",
        "voice_model": None, "cli_model": "ollama-qwen3-30b-a3b",
        "tts_provider": "kokoro:af_heart", "tts_voice": "af_heart",
        "allowed_tools": None,
    },
]


def _default_doc() -> dict[str, Any]:
    return {"active": "deepseek", "modes": [dict(m) for m in _BUILTINS]}


def _write_atomic(doc: dict[str, Any]) -> None:
    MODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, MODES_FILE)


def load() -> dict[str, Any]:
    """Return the modes doc, seeding built-ins if the file is missing/corrupt."""
    with _LOCK:
        if not MODES_FILE.exists():
            doc = _default_doc()
            _write_atomic(doc)
            return doc
        try:
            return json.loads(MODES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[modes] modes.json unreadable (%s); reseeding", e)
            try:
                MODES_FILE.replace(MODES_FILE.with_suffix(".json.bak"))
            except OSError:
                pass
            doc = _default_doc()
            _write_atomic(doc)
            return doc
