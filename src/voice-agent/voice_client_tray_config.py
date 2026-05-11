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
)


# ── Speech-LLM (voice-side) switching ───────────────────────────────
# Same file/endpoint pattern as CLI model but a switch DOES require
# a restart of the agent unit (its LLM is built once at session
# start; can't hot-swap). voice-client kicks `systemctl --user
# restart jarvis-voice-agent` after writing the file. The voice-
# client itself stays up — the SFU preserves the room while the
# agent rejoins.
SPEECH_MODEL_FILE: Path     = Path.home() / ".jarvis" / "voice-model"
DEFAULT_SPEECH_MODEL: str   = "llama-3.3-70b-versatile"

SPEECH_MODELS_AVAILABLE: tuple[str, ...] = (
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
    # Anthropic — Haiku 4.5. Added 2026-05-11 alongside the Anthropic
    # rung in build_dispatching_llm. Requires ANTHROPIC_API_KEY in env.
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
