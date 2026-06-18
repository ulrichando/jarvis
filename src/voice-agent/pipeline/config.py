"""Single source of truth for the voice-agent's env-var surface.

Pre-2026-05-10 the 61 `os.environ.get("JARVIS_*"|"LIVEKIT_*"|...)`
call sites were scattered across 30+ modules. A new dev wanting to
know "what knobs does this service expose?" had to grep. Now the
full surface lives in this file: read once at import, validated +
typed, exposed as module-level constants.

Usage from a caller:

    from pipeline.config import DISPATCH_DISABLED, GROQ_API_KEY
    if DISPATCH_DISABLED:
        ...

Or with the bundled `config` object:

    from pipeline.config import config
    if config.dispatch_disabled:
        ...

Legacy callers that still read via `os.environ.get(...)` continue
to work unchanged — this module READS the same env vars, it doesn't
override or rewrite them. Migration is incremental.

Conventions:
  * `_bool("X", default)`     — accepts "1"/"0", "true"/"false", "yes"/"no"
  * `_int(...)` / `_float(...)` — parses with fallback on ValueError
  * `_str(...)`               — returns "" for unset (never None)
  * Sensitive values (API keys, secrets) are NOT logged anywhere in
    this module — they're just read and passed through.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger("jarvis.config")


__all__ = [
    # Service shape
    "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    # Vendor API keys
    "GROQ_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "KIMI_API_KEY",
    # LLM / dispatcher
    "DISPATCH_DISABLED", "GRAPH_DISABLED", "DS_FALLBACK_MODEL",
    "ROUTER_PROVIDER", "ROUTER_MODEL", "ROUTER_TIMEOUT_MS",
    "TOKEN_AWARE_PRUNE", "KIMI_VOICE_EXPERIMENTAL",
    "LLM_IDLE_TIMEOUT", "CLI_TIMEOUT_S", "VALIDATOR_MODEL",
    "TTFW_TARGET_MS", "HANDOFF_CROSS_STREAM_GUARD", "CONFAB_DETECTOR",
    # Voice / TTS
    "TTS_VOICE", "EDGE_VOICE",
    "VOICE_BANTER", "VOICE_TASK", "VOICE_REASONING", "VOICE_EMOTIONAL",
    # Memory
    "MEMORY_CONSOLIDATOR", "MEMORY_CONSOLIDATE_EVERY_N", "MEMORY_TOP_N",
    # Quiet hours
    "QUIET_HOURS_START", "QUIET_HOURS_END", "QUIET_HOURS_WINDOW_SEC",
    # Face ID / vision
    "FACE_THRESHOLD", "FACE_ENROLL_FRAMES", "FACE_LIVENESS_FRAMES",
    "IR_DEVICE", "WEBCAM_DEVICE", "WEBCAM_RES",
    "SCREENSHOT_JPEG_Q", "SCREENSHOT_MAX_EDGE", "VISION_BACKEND",
    "SCREEN_OBSERVER_ENABLED", "SCREEN_OBSERVER_INTERVAL_S",
    "SCREEN_OBSERVER_MAX_AGE_S",
    # Browser / external
    "BROWSER_CDP_URL", "EXT_TIMEOUT_MS", "BRIDGE_URL", "LOCAL_API_TOKEN",
    # Voice client
    "VOICE_CLIENT_PORT", "VOICE_IDENTITY", "VOICE_LOG_LEVEL",
    "VOICE_ROOM", "VOICE_SESSION_ID",
    # Ollama (offline vision)
    "OLLAMA_URL", "OLLAMA_VISION_MODEL",
    # Display
    "DISPLAY", "XAUTHORITY",
    # Bundled namespace
    "config",
]


# ── Parsing helpers ──────────────────────────────────────────────────

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "t", "y"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "f", "n", ""})


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip().lower()
    if v in _TRUE_TOKENS:
        return True
    if v in _FALSE_TOKENS:
        return False
    logger.warning("[config] %s=%r is not a bool — using default=%s", name, v, default)
    return default


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        logger.warning("[config] %s=%r is not an int — using default=%d", name, v, default)
        return default


def _float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    try:
        return float(v)
    except ValueError:
        logger.warning("[config] %s=%r is not a float — using default=%s", name, v, default)
        return default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# ── Service shape ────────────────────────────────────────────────────

LIVEKIT_URL: str        = _str("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_API_KEY: str    = _str("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET: str = _str("LIVEKIT_API_SECRET")

DISPLAY: str    = _str("DISPLAY", ":0")
XAUTHORITY: str = _str("XAUTHORITY")


# ── Vendor API keys (NEVER logged) ───────────────────────────────────

GROQ_API_KEY: str     = _str("GROQ_API_KEY")
DEEPSEEK_API_KEY: str = _str("DEEPSEEK_API_KEY")
GOOGLE_API_KEY: str   = _str("GOOGLE_API_KEY")
KIMI_API_KEY: str     = _str("KIMI_API_KEY")


# ── LLM / dispatcher tuning ──────────────────────────────────────────

# Maya-class dispatcher build (per-route BANTER/TASK/REASONING/EMOTIONAL
# LLM). JARVIS_DISPATCH_DISABLED=1 reverts to single-LLM.
DISPATCH_DISABLED: bool = _bool("JARVIS_DISPATCH_DISABLED", False)

# LangGraph slow-path classifier. Default-on; kill-switch.
GRAPH_DISABLED: bool = _bool("JARVIS_GRAPH_DISABLED", False)

# DeepSeek model used as cross-provider FallbackAdapter target.
DS_FALLBACK_MODEL: str = _str("JARVIS_DS_FALLBACK_MODEL", "deepseek-v4-flash")

# Slow-path classifier LLM (BANTER/TASK/REASONING/EMOTIONAL routing).
ROUTER_PROVIDER: str   = _str("JARVIS_ROUTER_PROVIDER", "groq")
ROUTER_MODEL: str      = _str("JARVIS_ROUTER_MODEL", "llama-3.1-8b-instant")
ROUTER_TIMEOUT_MS: int = _int("JARVIS_ROUTER_TIMEOUT_MS", 800)

# Token-aware pre-flight + hard-prune when chat_ctx exceeds budget.
TOKEN_AWARE_PRUNE: bool = _bool("JARVIS_TOKEN_AWARE_PRUNE", True)

# Kimi K2.6 voice-supervisor experimental gate (broken — see CLAUDE.md).
KIMI_VOICE_EXPERIMENTAL: bool = _bool("JARVIS_KIMI_VOICE_EXPERIMENTAL", False)

# Idle-timeout wrapper around LLM streams (seconds).
LLM_IDLE_TIMEOUT: float = _float("JARVIS_LLM_IDLE_TIMEOUT", 30.0)

# Bounded CLI-tool delegation timeout (seconds).
CLI_TIMEOUT_S: float = _float("JARVIS_CLI_TIMEOUT_S", 60.0)

# Validator subagent inner-LLM model id.
VALIDATOR_MODEL: str = _str("JARVIS_VALIDATOR_MODEL", "llama-3.1-8b-instant")

# Time-to-first-word target for tuning probes (ms).
TTFW_TARGET_MS: int = _int("JARVIS_TTFW_TARGET_MS", 350)

# Cross-stream guard for handoff text (drops anticipatory text).
HANDOFF_CROSS_STREAM_GUARD: bool = _bool("JARVIS_HANDOFF_CROSS_STREAM_GUARD", True)

# Confab-detector kill-switch (default-on).
CONFAB_DETECTOR: bool = _bool("JARVIS_CONFAB_DETECTOR", True)


# ── Voice / TTS ──────────────────────────────────────────────────────

# Single-voice fallback for the non-dispatching TTS path.
TTS_VOICE: str = _str("JARVIS_TTS_VOICE", "troy")

# Edge TTS safety-net voice (used when Groq Orpheus has an outage).
EDGE_VOICE: str = _str("JARVIS_EDGE_VOICE", "en-US-ChristopherNeural")

# Per-route TTS voices when dispatcher is active.
VOICE_BANTER: str    = _str("JARVIS_VOICE_BANTER", "austin")
VOICE_TASK: str      = _str("JARVIS_VOICE_TASK", "troy")
VOICE_REASONING: str = _str("JARVIS_VOICE_REASONING", "troy")
VOICE_EMOTIONAL: str = _str("JARVIS_VOICE_EMOTIONAL", "daniel")




# ── Memory ───────────────────────────────────────────────────────────

MEMORY_CONSOLIDATOR: bool           = _bool("JARVIS_MEMORY_CONSOLIDATOR", True)
MEMORY_CONSOLIDATE_EVERY_N: int     = _int("JARVIS_MEMORY_CONSOLIDATE_EVERY_N", 10)
MEMORY_TOP_N: int                   = _int("JARVIS_MEMORY_TOP_N", 8)


# ── Quiet hours (OFF by default per user directive 2026-05-10) ───────

QUIET_HOURS_START: int      = _int("JARVIS_QUIET_START", 0)
QUIET_HOURS_END: int        = _int("JARVIS_QUIET_END", 0)
QUIET_HOURS_WINDOW_SEC: float = _float("JARVIS_QUIET_WINDOW_SEC", 1200.0)


# ── Face ID / vision ─────────────────────────────────────────────────

FACE_THRESHOLD: float    = _float("JARVIS_FACE_THRESHOLD", 0.45)
FACE_ENROLL_FRAMES: int  = _int("JARVIS_FACE_ENROLL_FRAMES", 5)
FACE_LIVENESS_FRAMES: int = _int("JARVIS_FACE_LIVENESS_FRAMES", 3)
IR_DEVICE: str           = _str("JARVIS_IR_DEVICE", "/dev/video2")
WEBCAM_DEVICE: str       = _str("JARVIS_WEBCAM_DEVICE", "/dev/video0")
WEBCAM_RES: str          = _str("JARVIS_WEBCAM_RES", "640x480")
SCREENSHOT_JPEG_Q: int   = _int("JARVIS_SCREENSHOT_JPEG_Q", 75)
SCREENSHOT_MAX_EDGE: int = _int("JARVIS_SCREENSHOT_MAX_EDGE", 1920)
VISION_BACKEND: str      = _str("JARVIS_VISION_BACKEND", "auto")

# Continuous screen-share observer (pipeline/screen_share_observer.py).
# When ENABLED and a SOURCE_SCREENSHARE track is subscribed, the
# observer polls vision_describe() every INTERVAL_S seconds on the
# latest cached frame and parks the text description on the session.
# screenshot() reads that cache so "what's on my screen?" returns in
# ~0s instead of ~4s. Designed 2026-05-11 evening after Gemini Live
# API smoke-test showed Live offered no advantage for our usage.
SCREEN_OBSERVER_ENABLED: bool   = _str("JARVIS_SCREEN_OBSERVER_ENABLED", "1") not in ("0", "false", "")
SCREEN_OBSERVER_INTERVAL_S: float = float(_str("JARVIS_SCREEN_OBSERVER_INTERVAL_S", "5.0"))
SCREEN_OBSERVER_MAX_AGE_S: float  = float(_str("JARVIS_SCREEN_OBSERVER_MAX_AGE_S", "10.0"))


# ── Browser / external services ──────────────────────────────────────

BROWSER_CDP_URL: str  = _str("JARVIS_BROWSER_CDP_URL", "http://127.0.0.1:9222")
EXT_TIMEOUT_MS: int   = _int("JARVIS_EXT_TIMEOUT_MS", 15000)
BRIDGE_URL: str       = _str("JARVIS_BRIDGE_URL", "http://127.0.0.1:8765")
LOCAL_API_TOKEN: str  = _str("JARVIS_LOCAL_API_TOKEN")


# ── Voice client (jarvis_voice_client.py) ────────────────────────────

VOICE_CLIENT_PORT: int = _int("JARVIS_VOICE_CLIENT_PORT", 8766)
VOICE_IDENTITY: str    = _str("JARVIS_VOICE_IDENTITY", "ulrich")
VOICE_LOG_LEVEL: str   = _str("JARVIS_VOICE_LOG_LEVEL", "INFO")
VOICE_ROOM: str        = _str("JARVIS_VOICE_ROOM", "jarvis")
VOICE_SESSION_ID: str  = _str("JARVIS_VOICE_SESSION_ID")


# ── Ollama (offline vision fallback) ─────────────────────────────────

OLLAMA_URL: str          = _str("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_VISION_MODEL: str = _str("JARVIS_OLLAMA_VISION_MODEL", "llava")


# ── Local offline fallback stack (LLM / STT / TTS / Vision) ──────────
# Last-resort local alternatives so JARVIS stays alive when ALL cloud
# providers are unreachable. Each component degrades INDEPENDENTLY via
# its own FallbackAdapter rung — there is no single "offline switch".
# Default OFF; cloud stays primary.
#
# NOTE: the LLM rung is read LIVE inside
# providers/llm.py::build_dispatching_llm (and the tray builders) from
# os.environ — NOT from these module constants — so unit tests can
# monkeypatch env and a tray restart picks up changes. These constants
# mirror the same names + defaults for operator discoverability and for
# the STT/TTS/vision build paths that consult them. Keep both in sync.
LOCAL_LLM_ENABLED: bool   = _bool("JARVIS_LOCAL_LLM_ENABLED", False)
LOCAL_LLM_URL: str        = _str("JARVIS_LOCAL_LLM_URL", "http://127.0.0.1:11434/v1")
LOCAL_LLM_MODEL: str      = _str("JARVIS_LOCAL_LLM_MODEL", "qwen3:14b")
LOCAL_LLM_API_KEY: str    = _str("JARVIS_LOCAL_LLM_API_KEY", "ollama")
LOCAL_LLM_TEMP: float     = _float("JARVIS_LOCAL_LLM_TEMP", 0.6)
LOCAL_LLM_TIMEOUT: float  = _float("JARVIS_LOCAL_LLM_TIMEOUT", 60.0)
LOCAL_LLM_ROUTES: str     = _str("JARVIS_LOCAL_LLM_ROUTES", "")  # csv; empty = all routes

# STT — faster-whisper (same Whisper family Groq uses), local last rung.
LOCAL_STT_ENABLED: bool   = _bool("JARVIS_LOCAL_STT_ENABLED", False)
LOCAL_STT_MODEL: str      = _str("JARVIS_LOCAL_STT_MODEL", "large-v3")
LOCAL_STT_DEVICE: str     = _str("JARVIS_LOCAL_STT_DEVICE", "auto")     # auto|cpu|cuda
LOCAL_STT_COMPUTE: str    = _str("JARVIS_LOCAL_STT_COMPUTE", "default") # e.g. int8, float16

# TTS — Piper (in-process, default) or Kokoro (separate OpenAI-compat
# /audio/speech server) — local last rung.
LOCAL_TTS_ENABLED: bool   = _bool("JARVIS_LOCAL_TTS_ENABLED", False)
LOCAL_TTS_ENGINE: str     = _str("JARVIS_LOCAL_TTS_ENGINE", "piper")    # piper|kokoro
LOCAL_TTS_VOICE: str      = _str("JARVIS_LOCAL_TTS_VOICE", "")          # engine default if empty
LOCAL_TTS_MODEL_PATH: str = _str("JARVIS_LOCAL_TTS_MODEL_PATH", "")     # piper: .onnx path
LOCAL_TTS_URL: str        = _str("JARVIS_LOCAL_TTS_URL", "http://127.0.0.1:8880/v1")  # kokoro server
LOCAL_TTS_SPEED: float    = _float("JARVIS_LOCAL_TTS_SPEED", 1.0)       # kokoro speech speed

# Vision — wire the existing OLLAMA_VISION_MODEL as an ACTIVE fallback
# rung for computer_use when Anthropic vision fails.
LOCAL_VISION_ENABLED: bool = _bool("JARVIS_LOCAL_VISION_ENABLED", False)


# ── Bundled namespace ────────────────────────────────────────────────
# Equivalent to the module constants above, just accessible as
# `config.dispatch_disabled` etc. Same values; pick whichever style
# the caller prefers.

@dataclass(frozen=True, slots=True)
class _Config:
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    display: str
    xauthority: str
    groq_api_key: str
    deepseek_api_key: str
    google_api_key: str
    kimi_api_key: str
    dispatch_disabled: bool
    graph_disabled: bool
    ds_fallback_model: str
    router_provider: str
    router_model: str
    router_timeout_ms: int
    token_aware_prune: bool
    kimi_voice_experimental: bool
    llm_idle_timeout: float
    cli_timeout_s: float
    validator_model: str
    ttfw_target_ms: int
    handoff_cross_stream_guard: bool
    confab_detector: bool
    tts_voice: str
    edge_voice: str
    voice_banter: str
    voice_task: str
    voice_reasoning: str
    voice_emotional: str
    memory_consolidator: bool
    memory_consolidate_every_n: int
    memory_top_n: int
    quiet_hours_start: int
    quiet_hours_end: int
    quiet_hours_window_sec: float
    face_threshold: float
    face_enroll_frames: int
    face_liveness_frames: int
    ir_device: str
    webcam_device: str
    webcam_res: str
    screenshot_jpeg_q: int
    screenshot_max_edge: int
    vision_backend: str
    browser_cdp_url: str
    ext_timeout_ms: int
    bridge_url: str
    local_api_token: str
    voice_client_port: int
    voice_identity: str
    voice_log_level: str
    voice_room: str
    voice_session_id: str
    ollama_url: str
    ollama_vision_model: str


config = _Config(
    livekit_url=LIVEKIT_URL,
    livekit_api_key=LIVEKIT_API_KEY,
    livekit_api_secret=LIVEKIT_API_SECRET,
    display=DISPLAY,
    xauthority=XAUTHORITY,
    groq_api_key=GROQ_API_KEY,
    deepseek_api_key=DEEPSEEK_API_KEY,
    google_api_key=GOOGLE_API_KEY,
    kimi_api_key=KIMI_API_KEY,
    dispatch_disabled=DISPATCH_DISABLED,
    graph_disabled=GRAPH_DISABLED,
    ds_fallback_model=DS_FALLBACK_MODEL,
    router_provider=ROUTER_PROVIDER,
    router_model=ROUTER_MODEL,
    router_timeout_ms=ROUTER_TIMEOUT_MS,
    token_aware_prune=TOKEN_AWARE_PRUNE,
    kimi_voice_experimental=KIMI_VOICE_EXPERIMENTAL,
    llm_idle_timeout=LLM_IDLE_TIMEOUT,
    cli_timeout_s=CLI_TIMEOUT_S,
    validator_model=VALIDATOR_MODEL,
    ttfw_target_ms=TTFW_TARGET_MS,
    handoff_cross_stream_guard=HANDOFF_CROSS_STREAM_GUARD,
    confab_detector=CONFAB_DETECTOR,
    tts_voice=TTS_VOICE,
    edge_voice=EDGE_VOICE,
    voice_banter=VOICE_BANTER,
    voice_task=VOICE_TASK,
    voice_reasoning=VOICE_REASONING,
    voice_emotional=VOICE_EMOTIONAL,
    memory_consolidator=MEMORY_CONSOLIDATOR,
    memory_consolidate_every_n=MEMORY_CONSOLIDATE_EVERY_N,
    memory_top_n=MEMORY_TOP_N,
    quiet_hours_start=QUIET_HOURS_START,
    quiet_hours_end=QUIET_HOURS_END,
    quiet_hours_window_sec=QUIET_HOURS_WINDOW_SEC,
    face_threshold=FACE_THRESHOLD,
    face_enroll_frames=FACE_ENROLL_FRAMES,
    face_liveness_frames=FACE_LIVENESS_FRAMES,
    ir_device=IR_DEVICE,
    webcam_device=WEBCAM_DEVICE,
    webcam_res=WEBCAM_RES,
    screenshot_jpeg_q=SCREENSHOT_JPEG_Q,
    screenshot_max_edge=SCREENSHOT_MAX_EDGE,
    vision_backend=VISION_BACKEND,
    browser_cdp_url=BROWSER_CDP_URL,
    ext_timeout_ms=EXT_TIMEOUT_MS,
    bridge_url=BRIDGE_URL,
    local_api_token=LOCAL_API_TOKEN,
    voice_client_port=VOICE_CLIENT_PORT,
    voice_identity=VOICE_IDENTITY,
    voice_log_level=VOICE_LOG_LEVEL,
    voice_room=VOICE_ROOM,
    voice_session_id=VOICE_SESSION_ID,
    ollama_url=OLLAMA_URL,
    ollama_vision_model=OLLAMA_VISION_MODEL,
)
