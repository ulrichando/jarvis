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
import os
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
    "active_tts_provider",
    "active_stt_engine",
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
# Default: claude-sonnet-4-6 (2026-05-18) per user-driven curation.
# Sonnet 4.6 leads τ-bench 87.5% for multi-turn tool use — the CLI is
# pure tool-driven coding work, so the best tool-caller in the user's
# stack is the right default. Previous default (deepseek-v4-pro) was
# retired alongside the voice-side cleanup: Artificial Analysis 2026
# pegs V4 Pro at 94% hallucination rate; voice-side had already
# retired it 2026-05-16 after the live capture turn-160 Bosnian
# hallucination. The CLI picker now matches the voice picker
# discipline ("good or best only").
DEFAULT_CLI_MODEL: str   = "claude-sonnet-4-6"

# Curated 2026-05-18 to mirror the speech-side picker discipline.
# Show only the "good or best" tier. Dropped IDs stay in
# jarvis_agent.py's CLI_MODELS dict so the env-var passthrough still
# resolves them if a CLI sub-process re-reads them; they just don't
# appear in the tray.
CLI_MODELS_AVAILABLE: tuple[str, ...] = (
    # Anthropic — tool-calling tier-leader. Sonnet is the default
    # (best τ-bench). Opus for the hardest multi-step work. Haiku
    # for fast single-shot tool calls.
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-haiku-4-5",
    # OpenAI — alternatives for tool-calling work. gpt-5.1 has the
    # best OpenAI-tier tool-call accuracy; mini is the speed-balance.
    "gpt-5.1",
    "gpt-5-mini",
    # Groq — strongest open-weights tool-caller in the stack.
    "qwen/qwen3-32b",
    # DeepSeek — re-added 2026-06-23 per user request (their daily-driver
    # CLI model) despite the documented 94% hallucination rate.
    "deepseek-v4-pro",
    # Kimi — K2.7 code model (Moonshot). Strong agentic tool-caller; added
    # 2026-06-23 per user request. Routes via the proxy's kimi provider.
    "kimi-k2.7-code",
    #
    # Dropped 2026-05-18 (still in jarvis_agent.py CLI_MODELS for
    # env-var passthrough resolution if needed, but hidden here):
    #   - deepseek-chat (V3)       (non-thinking baseline)
    #   - deepseek-reasoner        (deprecated, replaced by V4)
    #   - deepseek-v4-flash        (96% hallucination, Artificial Analysis)
    #   - deepseek-v4-pro          (94% hallucination; was the prior default)
    #   - llama-3.3-70b-versatile  (generalist, no tool-call edge)
    #   - llama-4-scout            (weaker tool calling)
    #   - openai/gpt-oss-120b      (qwen3-32b covers the Groq slot)
    #   - gpt-5-nano               (in-code: weakest tool calling)
    #   - gpt-5, gpt-4o            (no edge over mini/5.1)
    #   - gpt-5.1-chat-latest      (redundant with gpt-5.1)
)


# ── Speech-LLM (voice-side) switching ───────────────────────────────
# Same file/endpoint pattern as CLI model but a switch DOES require
# a restart of the agent unit (its LLM is built once at session
# start; can't hot-swap). voice-client kicks `systemctl --user
# restart jarvis-voice-agent` after writing the file. The voice-
# client itself stays up — the SFU preserves the room while the
# agent rejoins.
SPEECH_MODEL_FILE: Path     = Path.home() / ".jarvis" / "voice-model"
# Default: claude-haiku-4-5 (2026-05-18). Best TTFT/tool-quality
# balance per public 2026 voice-loop benchmarks (~0.7s TTFT vs
# gpt-5-mini ~1.34s). Picked after the researcher's cross-provider
# ranking and the user's switch decision. Requires ANTHROPIC_API_KEY.
# Previous default (gpt-5-mini) remains in the picker as the OpenAI
# alternative.
DEFAULT_SPEECH_MODEL: str   = "claude-haiku-4-5"

# 2026-05-18 picker curation: dropped the weak / redundant / retired
# entries per user request — show only the "good or best" tier the
# user would realistically want to switch between. Dropped IDs stay
# REGISTERED in providers/llm.py SPEECH_MODELS so the dispatcher's
# fallback chains still work; they just don't appear in the tray.
SPEECH_MODELS_AVAILABLE: tuple[str, ...] = (
    # Anthropic — voice-loop sweet spot. Haiku is the default;
    # Sonnet for tool-heavy multi-step work; Opus for extended
    # reasoning sessions through voice.
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    # OpenAI — alternatives if Anthropic credit dries up. gpt-5-mini
    # was the previous default; gpt-5.1 is the best tool-calling
    # accuracy in OpenAI's tier (~500ms slower TTFT than mini).
    "gpt-5-mini",
    "gpt-5.1",
    # Groq — only the strongest tool-caller in the Groq tier
    # (qwen3-32b: BFCL v3 #2 open-weights, <400ms TTFT). For no-
    # OpenAI-quota / no-Anthropic-credit days.
    "qwen/qwen3-32b",
    # DeepSeek — v4-flash re-added 2026-06-23 per user request (daily-driver
    # voice model). It's in the tray menu + providers/llm.py SPEECH_MODELS but
    # was missing here, so read_speech_model() rejected the pinned id and
    # /status fell back to DEFAULT_SPEECH_MODEL (Haiku) — the tray header
    # showed "Speech: Haiku" while the agent actually ran DeepSeek. Listing it
    # makes /status report the real pinned model.
    "deepseek-v4-flash",
    # Local (Ollama) — on-device voice brain; the tray offers these when the
    # model is pulled. Listed here so /status reports the active local pick
    # instead of falling back to the Haiku default.
    "ollama/qwen3:30b-a3b",
    "ollama/gpt-oss:120b",
    # Kimi — K2.7 code model (Moonshot), added 2026-06-23 per user request for
    # voice tool-calling. CAVEAT: K2.6 broke the voice path (spontaneous
    # built-in web_search tool-call → Moonshot 400 → supervisor wedge → silent).
    # K2.7-code is UNVERIFIED on the live voice loop — if JARVIS goes silent
    # after switching to it, that's the same bug; switch back to Haiku.
    "kimi-k2.7-code",
    #
    # Dropped 2026-05-18 (registered in providers/llm.py, hidden here):
    #   - gpt-5-nano               (in-code: weakest tool calling)
    #   - gpt-5, gpt-4o            (no edge over mini/5.1)
    #   - gpt-5.1-chat-latest      (redundant with gpt-5.1)
    #   - llama-3.3-70b-versatile  (kept as dispatcher TASK fallback)
    #   - llama-3.1-8b-instant     (kept as BANTER specialist)
    #   - llama-4-scout            (was EMOTIONAL — upgraded to Haiku)
    #   - openai/gpt-oss-120b      (qwen3-32b covers the Groq slot)
    #   - deepseek-chat (V3)       (non-thinking baseline)
    #   - deepseek-v4-pro          (retired 2026-05-16; 94% hallucination)
    # (deepseek-v4-flash was here too until 2026-06-23 — re-added above.)
    # Kimi K2.6 entries remain gated behind
    # JARVIS_KIMI_VOICE_EXPERIMENTAL=1 — broken on third-party hosts
    # (spontaneous web_search tool-call emission per vLLM + Hermes-agent
    # confirms).
)


# ── TTS provider switching ──────────────────────────────────────────
# Format: "<provider>:<voice_id_or_name>". Engines: `kokoro:<voice>`
# (on-device, the default) and `edge:<voice>` (Microsoft Edge-TTS,
# auth-free). Groq Orpheus was removed 2026-06-29 (full-Groq-eradication
# pass); ElevenLabs was removed 2026-05-01.
TTS_PROVIDER_FILE: Path = Path.home() / ".jarvis" / "tts-provider"

TTS_PROVIDERS_AVAILABLE: dict[str, str] = {
    # On-device · Kokoro (af_heart) — the default; runs locally via the
    # kokoro-tts container. These gate the /tts-provider POST + label GET.
    "kokoro:af_heart": "Kokoro · Heart (local)",
    # Online · Microsoft Edge-TTS (auth-free, online).
    "edge:en-US-GuyNeural":         "Edge · Guy",
    "edge:en-US-ChristopherNeural": "Edge · Christopher",
    "edge:en-US-JennyNeural":       "Edge · Jenny",
    "edge:en-US-AriaNeural":        "Edge · Aria",
    "edge:en-GB-RyanNeural":        "Edge · Ryan",
    "edge:en-GB-SoniaNeural":       "Edge · Sonia",
}


def default_tts_provider() -> str:
    return "kokoro:af_heart"


def ensure_tts_provider_file() -> None:
    if not TTS_PROVIDER_FILE.exists():
        TTS_PROVIDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TTS_PROVIDER_FILE.write_text(default_tts_provider() + "\n", encoding="utf-8")


def active_tts_provider(current: str) -> str:
    """The TTS provider/engine ACTUALLY in effect.

    ``current`` is the ~/.jarvis/tts-provider spec written by the tray. As of
    2026-06-25 that spec is AUTHORITATIVE for the engine — build_tts_chain picks
    Kokoro / Edge from its prefix (``kokoro:`` / ``edge:``) — so report it
    as-is. Two exceptions:
      - ``JARVIS_LOCAL_TTS_ONLY=1`` forces on-device regardless of the pick.
      - an empty / prefix-less spec falls back to the local-first default when
        ``JARVIS_LOCAL_TTS_PRIMARY=1``, else returns ``current`` unchanged.
    Replaces the old behavior where LOCAL_TTS_PRIMARY made this ALWAYS report
    Kokoro — which lied the moment an online voice was picked.
    """
    def _local() -> str:
        engine = (os.environ.get("JARVIS_LOCAL_TTS_ENGINE") or "kokoro").strip() or "kokoro"
        if engine == "kokoro":
            voice = (os.environ.get("JARVIS_LOCAL_TTS_VOICE") or "af_heart").strip() or "af_heart"
            return f"kokoro:{voice}"
        return f"{engine}:local"

    if os.environ.get("JARVIS_LOCAL_TTS_ONLY") == "1":
        return _local()
    if current and ":" in current:
        return current
    if os.environ.get("JARVIS_LOCAL_TTS_PRIMARY") == "1":
        return _local()
    return current


def active_stt_engine() -> str:
    """The STT engine ACTUALLY in effect — distinct from ``speech_model``,
    which is the reply LLM, not the transcriber. faster-whisper on-device when
    the local STT env flags are set (``JARVIS_LOCAL_STT_PRIMARY`` /
    ``JARVIS_STT_LOCAL_ONLY``); otherwise the active cloud STT. Lets the tray
    show that STT is local (it had no STT label before — part of the same bug)."""
    if (os.environ.get("JARVIS_LOCAL_STT_PRIMARY") == "1"
            or os.environ.get("JARVIS_STT_LOCAL_ONLY") == "1"):
        model = (os.environ.get("JARVIS_LOCAL_STT_MODEL") or "large-v3-turbo").strip() or "large-v3-turbo"
        # faster-whisper drops the family prefix (its model id is e.g.
        # "large-v3-turbo"); show the familiar "whisper-…" spelling.
        if not model.startswith("whisper"):
            model = f"whisper-{model}"
        return f"{model} (local)"
    if os.environ.get("JARVIS_DEEPGRAM_DISABLED") == "1" or not os.environ.get("DEEPGRAM_API_KEY"):
        # No Deepgram + local STT flags unset → the chain falls to the
        # on-device faster-whisper rung (Groq Whisper was removed 2026-06-29).
        return "whisper-large-v3-turbo (local)"
    return "deepgram:nova-3"


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
