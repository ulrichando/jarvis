"""Tray-config layer for the voice client.

State files + model allowlists + small readers that the tray UI
manipulates via the HTTP control plane:

  - `~/.jarvis/cli-model`          — CLI model id (DeepSeek family +
                                     a few others). Read by every
                                     run_jarvis_cli spawn, no restart
                                     needed on switch.
  - `~/.jarvis/voice-model`        — Speech-LLM id. Requires a
                                     jarvis-voice-agent restart on
                                     switch (LLM built once per
                                     session).
  - `~/.jarvis/tts-provider`       — "<provider>:<voice>" string.
                                     Only `groq:<voice>` accepted
                                     post-2026-05-01.
  - `~/.jarvis/.tool-running`      — agent flag, presence = a tool
                                     is currently executing.
  - `~/.jarvis/.silent-mode`       — agent flag, presence = silent
                                     mode active.
  - `~/.jarvis/.agent-thinking`    — agent flag, mtime = LLM is
                                     generating (TTL-aged).

Pure config + read-only helpers — no shared state with the rest of
the voice client, no I/O beyond file reads / one-time defaults
write. Hoisted from `jarvis_voice_client.py` 2026-05-10 (Step 7
of the audit).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path


logger = logging.getLogger("jarvis.voice_client")


__all__ = [
    # Tray-driven model picker files
    "CLI_MODEL_FILE",
    "DEFAULT_CLI_MODEL",
    "CLI_MODELS_AVAILABLE",
    "SPEECH_MODEL_FILE",
    "DEFAULT_SPEECH_MODEL",
    "SPEECH_MODELS_AVAILABLE",
    "TTS_PROVIDER_FILE",
    "TTS_PROVIDERS_AVAILABLE",
    # Voice mode (text vs OpenAI Realtime API) — 2026-05-15
    "VOICE_MODE_FILE",
    "DEFAULT_VOICE_MODE",
    "VOICE_MODES_AVAILABLE",
    "REALTIME_MODEL_FILE",
    "DEFAULT_REALTIME_MODEL",
    "REALTIME_MODELS_AVAILABLE",
    "REALTIME_VOICE_FILE",
    "DEFAULT_REALTIME_VOICE",
    "REALTIME_VOICES_AVAILABLE",
    # State flag files
    "TOOL_BUSY_FILE",
    "SILENT_MODE_FILE",
    "AGENT_THINKING_FILE",
    "AGENT_THINKING_MAX_AGE",
    # Helpers
    "default_tts_provider",
    "ensure_tts_provider_file",
    "read_speech_model",
    "read_cli_model",
    "read_voice_mode",
    "read_realtime_model",
    "read_realtime_voice",
    "agent_is_thinking",
]


# ── CLI model switching ─────────────────────────────────────────────
# The tray POSTs to /cli-model; we write the chosen model ID to this
# file. The voice-agent's run_jarvis_cli reads the file on every
# spawn and exports JARVIS_PROVIDER + JARVIS_MODEL to the CLI
# subprocess — so switching takes effect on the very next tool call,
# no restart required. start.sh also reads this file so interactive
# terminal sessions stay in sync.
CLI_MODEL_FILE: Path     = Path.home() / ".jarvis" / "cli-model"
DEFAULT_CLI_MODEL: str   = "deepseek-v4-pro"

# Whitelist mirroring CLI_MODELS in jarvis_agent.py — duplicated as
# a literal tuple so the voice-client doesn't have to import heavy
# livekit plugin machinery just to validate a string.
CLI_MODELS_AVAILABLE: tuple[str, ...] = (
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-120b",
    # Anthropic Claude — added 2026-05-11. Three tiers mirroring the
    # Claude Code /model picker: Opus 4.7 (most capable), Sonnet 4.6
    # (everyday), Haiku 4.5 (fastest). Matched in the CLI's
    # jarvisModelRegistry under provider='anthropic'.
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    # OpenAI proper — full GPT-5 family + GPT-4o, added 2026-05-15.
    # Need the matching provider entry in the CLI-side
    # jarvisModelRegistry for `jarvis` to actually consume these; until
    # that's confirmed, the tray exposes them but the CLI may reject.
    "gpt-5-nano",
    "gpt-5-mini",
    "gpt-5",
    "gpt-5.1",
    "gpt-5.1-chat-latest",
    "gpt-4o",
)


# ── Speech-LLM (voice-side) switching ───────────────────────────────
# Same file/endpoint pattern as CLI model but a switch DOES require
# a restart of the agent unit (its LLM is built once at session
# start; can't hot-swap). voice-client kicks `systemctl --user
# restart jarvis-voice-agent` after writing the file. The voice-
# client itself stays up — the SFU preserves the room while the
# agent rejoins.
SPEECH_MODEL_FILE: Path     = Path.home() / ".jarvis" / "voice-model"
# OpenAI gpt-5-mini default since 2026-05-15: Anthropic credit pool ran
# out, Groq llama remains as a backup tray pick. gpt-5-mini gives the
# best fast-tool-calling balance on api.openai.com for the voice loop.
DEFAULT_SPEECH_MODEL: str   = "gpt-5-mini"

SPEECH_MODELS_AVAILABLE: tuple[str, ...] = (
    # OpenAI proper — Chat Completions-compatible GPT-5 family + gpt-4o
    # legacy. Added 2026-05-15. Order matches latency tier (fastest →
    # slowest). gpt-5-pro and gpt-5-codex appear in /v1/models but only
    # accept /v1/responses requests — lk_openai uses Chat Completions
    # and would error every supervisor turn, so they're excluded here.
    "gpt-5-nano",
    "gpt-5-mini",
    "gpt-5",
    "gpt-5.1",
    "gpt-5.1-chat-latest",
    "gpt-4o",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    # DeepSeek family — re-enabled after deepseek_roundtrip.install()
    # patches livekit-plugins-openai to echo reasoning_content on
    # assistant tool-call messages.
    "deepseek-chat",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    # Anthropic — added 2026-05-11 alongside the Anthropic rung in
    # build_dispatching_llm. Requires ANTHROPIC_API_KEY in env. Kept
    # in the picker even with credits exhausted so the entries return
    # automatically when the account is topped up.
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    # Kimi K2.6 voice entries are DISABLED 2026-05-05. K2.6 emits
    # built-in tool calls (web_search, etc.) that aren't in
    # request.tools, and Moonshot's API rejects every such request
    # with `tool call validation failed`. See the corresponding gate
    # in jarvis_agent.py SPEECH_MODELS — the entries return when
    # JARVIS_KIMI_VOICE_EXPERIMENTAL=1.
)


# ── TTS provider switching ──────────────────────────────────────────
# Format: "<provider>:<voice_id_or_name>". Only `groq:<voice>` is
# supported post-2026-05-01 (ElevenLabs removed).
TTS_PROVIDER_FILE: Path = Path.home() / ".jarvis" / "tts-provider"

TTS_PROVIDERS_AVAILABLE: dict[str, str] = {
    "groq:troy":   "Groq Orpheus · Troy",
    "groq:austin": "Groq Orpheus · Austin",
}


def default_tts_provider() -> str:
    return "groq:troy"


def ensure_tts_provider_file() -> None:
    if not TTS_PROVIDER_FILE.exists():
        TTS_PROVIDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TTS_PROVIDER_FILE.write_text(default_tts_provider() + "\n", encoding="utf-8")


# ── Voice mode: text-LLM chain vs OpenAI Realtime API ──────────────
# `text` is the historic JARVIS path (separate STT + supervisor LLM
# + Orpheus TTS, configurable via SPEECH_MODEL_FILE above). `realtime`
# swaps all three for a single OpenAI RealtimeModel — voice generated
# natively by the model, sub-second latency, native interruption. The
# tray writes this file; jarvis_agent reads it on session start and
# forks the AgentSession build accordingly. Switch ALWAYS requires
# an agent restart since the session topology changes.
# Default = `text` so a key-less / cost-cautious install stays on the
# cheap path. Flip via tray or `echo realtime > ~/.jarvis/voice-mode`.
VOICE_MODE_FILE: Path        = Path.home() / ".jarvis" / "voice-mode"
DEFAULT_VOICE_MODE: str      = "text"
VOICE_MODES_AVAILABLE: tuple[str, ...] = ("text", "realtime")

# Realtime API model picker — only meaningful when VOICE_MODE_FILE is
# `realtime`. Both Chat-Completions-only IDs (gpt-5-pro / gpt-5-codex)
# are EXCLUDED here too — they're Responses-only and don't have a
# Realtime endpoint. The two listed have parity feature sets; mini is
# ~3× cheaper, full is ~25% better at long-form reasoning.
REALTIME_MODEL_FILE: Path    = Path.home() / ".jarvis" / "realtime-model"
DEFAULT_REALTIME_MODEL: str  = "gpt-realtime-mini"
REALTIME_MODELS_AVAILABLE: tuple[str, ...] = (
    "gpt-realtime",
    "gpt-realtime-mini",
)

# Realtime voice picker (OpenAI's built-in voices for the Realtime API).
# Default `marin` matches the lk_openai plugin's default. All 9 voices
# work without account-tier gating.
REALTIME_VOICE_FILE: Path    = Path.home() / ".jarvis" / "realtime-voice"
DEFAULT_REALTIME_VOICE: str  = "marin"
REALTIME_VOICES_AVAILABLE: tuple[str, ...] = (
    "marin", "alloy", "ash", "ballad", "coral", "echo",
    "sage", "shimmer", "verse",
)


# ── State flag files (set/cleared by jarvis_agent) ──────────────────
# Same paths as jarvis_agent.py's `_TOOL_BUSY_FILE` etc. — written
# when a tool starts, deleted when it ends. Voice-client polls
# existence (cheap stat call) on every /status hit.
TOOL_BUSY_FILE: Path   = Path.home() / ".jarvis" / ".tool-running"
SILENT_MODE_FILE: Path = Path.home() / ".jarvis" / ".silent-mode"

# Agent's LLM-thinking flag. Presence means LLM is generating.
# Staleness check: if the file is older than AGENT_THINKING_MAX_AGE
# we ignore it. Handles the "agent decided to stay silent" case
# (directed-at-me filter rejects an ambient mic trigger): no
# assistant turn ever lands to clear the flag, but it goes stale
# and the tray drops gold automatically.
#
# 2026-05-02: bumped 10s → 60s. The agent now refreshes the flag
# on every `agent_state_changed → thinking` event, so under normal
# operation it never hits the TTL. The 60s ceiling is purely a
# safety belt against a stuck agent process leaving stale flags.
AGENT_THINKING_FILE: Path = Path.home() / ".jarvis" / ".agent-thinking"
AGENT_THINKING_MAX_AGE: float = 60.0  # seconds


# ── Readers ─────────────────────────────────────────────────────────

def read_speech_model() -> str:
    """Read the active speech-LLM id from the tray file, or the
    default if missing / unrecognized."""
    try:
        name = SPEECH_MODEL_FILE.read_text(encoding="utf-8").strip()
        if name in SPEECH_MODELS_AVAILABLE:
            return name
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"could not read {SPEECH_MODEL_FILE}: {e}")
    return DEFAULT_SPEECH_MODEL


def read_voice_mode() -> str:
    """Read the active voice mode (`text` or `realtime`) from the
    tray file. Falls back to the safe default (`text`) if missing
    or unrecognized — so a fresh install never accidentally lands on
    the 10×-more-expensive Realtime path."""
    try:
        name = VOICE_MODE_FILE.read_text(encoding="utf-8").strip()
        if name in VOICE_MODES_AVAILABLE:
            return name
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"could not read {VOICE_MODE_FILE}: {e}")
    return DEFAULT_VOICE_MODE


def read_realtime_model() -> str:
    """Read the chosen OpenAI Realtime model. Only meaningful when
    `read_voice_mode() == "realtime"`; otherwise jarvis_agent ignores
    it and uses the text-mode supervisor model instead."""
    try:
        name = REALTIME_MODEL_FILE.read_text(encoding="utf-8").strip()
        if name in REALTIME_MODELS_AVAILABLE:
            return name
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"could not read {REALTIME_MODEL_FILE}: {e}")
    return DEFAULT_REALTIME_MODEL


def read_realtime_voice() -> str:
    """Read the chosen OpenAI Realtime voice. Only meaningful when
    `read_voice_mode() == "realtime"`; otherwise the text-mode TTS
    provider (TTS_PROVIDER_FILE) decides the voice."""
    try:
        name = REALTIME_VOICE_FILE.read_text(encoding="utf-8").strip()
        if name in REALTIME_VOICES_AVAILABLE:
            return name
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"could not read {REALTIME_VOICE_FILE}: {e}")
    return DEFAULT_REALTIME_VOICE


def read_cli_model() -> str:
    """Read the active CLI model id from the tray file, or the
    default if missing / unrecognized."""
    try:
        name = CLI_MODEL_FILE.read_text(encoding="utf-8").strip()
        if name in CLI_MODELS_AVAILABLE:
            return name
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"could not read {CLI_MODEL_FILE}: {e}")
    return DEFAULT_CLI_MODEL


def agent_is_thinking() -> bool:
    """True if the thinking flag file exists AND is recent enough."""
    try:
        # Use mtime — the agent rewrites the file on each new turn,
        # so stat is enough; we don't need to read the contents.
        age = time.time() - AGENT_THINKING_FILE.stat().st_mtime
        return age < AGENT_THINKING_MAX_AGE
    except FileNotFoundError:
        return False
    except Exception:
        return False
