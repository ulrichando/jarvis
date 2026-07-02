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


def _load_unlocked() -> dict[str, Any]:
    """Read/seed the modes doc. Caller must hold _LOCK."""
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


def load() -> dict[str, Any]:
    """Return the modes doc, seeding built-ins if the file is missing/corrupt."""
    with _LOCK:
        return _load_unlocked()


_JD = Path.home() / ".jarvis"
_F_VOICE_MODE       = _JD / "voice-mode"
_F_VOICE_MODEL      = _JD / "voice-model"
_F_CLI_MODEL        = _JD / "cli-model"
_F_TTS_PROVIDER     = _JD / "tts-provider"
_F_VOICE_TTS_VOICE  = _JD / "voice-tts-voice"
_F_MODE_ALLOWED_TOOLS = _JD / "mode-allowed-tools"


def _write_setting(path: Path, value: Optional[str]) -> None:
    """Write a single ~/.jarvis setting file atomically. None → leave untouched."""
    if value is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(value) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def get_mode(mode_id: str) -> Optional[dict[str, Any]]:
    return next((m for m in load()["modes"] if m["id"] == mode_id), None)


def resolve(mode_id: str) -> dict[str, Any]:
    """Return a mode dict; raise KeyError if unknown."""
    m = get_mode(mode_id)
    if m is None:
        raise KeyError(f"unknown mode: {mode_id!r}")
    return m


def apply(mode_id: str) -> dict[str, Any]:
    """Write all underlying setting files for `mode_id` + set it active.
    Caller is responsible for restarting the agent."""
    m = resolve(mode_id)
    _write_setting(_F_VOICE_MODE, m.get("voice_mode") or "cloud")
    _write_setting(_F_VOICE_MODEL, m.get("voice_model"))
    _write_setting(_F_CLI_MODEL, m.get("cli_model"))
    _write_setting(_F_TTS_PROVIDER, m.get("tts_provider"))
    _write_setting(_F_VOICE_TTS_VOICE, m.get("tts_voice"))
    allowed = m.get("allowed_tools")
    _write_setting(_F_MODE_ALLOWED_TOOLS, "\n".join(allowed) if allowed else "")
    with _LOCK:
        doc = _load_unlocked()
        doc["active"] = mode_id
        _write_atomic(doc)
    logger.info("[modes] applied %s", mode_id)
    return m


# ---------------------------------------------------------------------------
# Per-mode tool allowlist helpers
# ---------------------------------------------------------------------------

# Tools ALWAYS available regardless of a mode's allowlist, so a mode can't brick
# the assistant (it still needs to talk + clarify + remember).
CORE_TOOLS: frozenset[str] = frozenset({"clarify", "memory"})


def active_allowed_tools() -> Optional[set[str]]:
    """The active mode's tool allowlist as a set, or None for 'no restriction'.
    Read from the file (not load()) so it's cheap + restart-fresh."""
    try:
        raw = _F_MODE_ALLOWED_TOOLS.read_text(encoding="utf-8")
    except OSError:
        return None
    names = {ln.strip() for ln in raw.splitlines() if ln.strip()}
    return names or None


def tool_is_mode_allowed(name: str) -> bool:
    allow = active_allowed_tools()
    if allow is None:
        return True
    return name in allow or name in CORE_TOOLS


# ---------------------------------------------------------------------------
# Store mutation helpers
# ---------------------------------------------------------------------------


def create(mode: dict[str, Any]) -> None:
    """Append a new mode to the store. Raises ValueError if the id already exists."""
    with _LOCK:
        doc = _load_unlocked()
        if any(m["id"] == mode["id"] for m in doc["modes"]):
            raise ValueError(f"mode already exists: {mode['id']!r}")
        doc["modes"].append(mode)
        _write_atomic(doc)


def update(mode_id: str, patch: dict[str, Any]) -> None:
    """Merge `patch` into an existing mode (id field is immutable). Raises KeyError if not found."""
    with _LOCK:
        doc = _load_unlocked()
        m = next((m for m in doc["modes"] if m["id"] == mode_id), None)
        if m is None:
            raise KeyError(mode_id)
        m.update({k: v for k, v in patch.items() if k != "id"})
        _write_atomic(doc)


def delete(mode_id: str) -> None:
    """Remove a mode. Raises ValueError if it is the currently active mode."""
    with _LOCK:
        doc = _load_unlocked()
        if doc["active"] == mode_id:
            raise ValueError("cannot delete the active mode; switch first")
        doc["modes"] = [m for m in doc["modes"] if m["id"] != mode_id]
        _write_atomic(doc)
