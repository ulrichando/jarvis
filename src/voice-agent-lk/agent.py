"""JARVIS headless realtime voice agent (LiveKit worker, VPS/CPU).

P2 of the self-hosted LiveKit voice pipeline (see
docs/superpowers/specs/2026-07-12-livekit-realtime-voice-design.md in
jarvis-android). Per turn: Silero VAD → faster-whisper (CPU int8) →
Claude via the JARVIS gateway (Anthropic-compatible /v1/messages) →
Edge TTS. Barge-in is the framework's VAD interruption — the phone
client's WebRTC AEC keeps the agent's own TTS out of the mic path.

Dispatch: automatic (no agent_name) — this LiveKit deployment exists
only for JARVIS voice, so the worker joins every room the token
endpoint creates.

Env (see .env.example):
  LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET — LiveKit server
  JARVIS_PROXY_JWT_SECRET — mints per-job gateway bearer tokens
  JARVIS_GATEWAY_URL      — default http://localhost:4000
  VOICE_LLM_MODEL         — default claude-sonnet-4-6 (gateway registry id)
  VOICE_STT_MODEL         — default base.en (faster-whisper, cpu/int8)
  VOICE_TTS_VOICE         — default en-GB-RyanNeural (matches Android)
  VOICE_GREETING          — optional spoken line when a user joins
"""
from __future__ import annotations

import logging
import os

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    metrics,
)
from livekit.plugins import anthropic, silero

import proxy_token
import stt_local
import tts_edge

logger = logging.getLogger("voice-agent")

DEFAULT_GATEWAY_URL = "http://localhost:4000"
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"  # verified in the gateway model registry

INSTRUCTIONS = """\
You are JARVIS, a voice assistant. You are talking with the user over a
realtime voice call, so:
- Keep replies short and conversational — one to three sentences unless
  the user clearly asks for detail.
- Plain spoken prose only: no markdown, no bullet lists, no code blocks,
  no emojis. Spell out anything that must be read aloud.
- If a transcription seems garbled or cut off, ask a brief clarifying
  question instead of guessing.
"""


def prewarm(proc: JobProcess) -> None:
    """Load Silero VAD + the whisper model before the first job."""
    proc.userdata["vad"] = silero.VAD.load(
        # Slightly longer than the default 0.55s — CPU whisper is
        # finals-only, so premature end-of-speech costs a whole re-turn.
        min_silence_duration=0.6,
    )
    stt_inst = stt_local.build_stt()
    # Load the ctranslate2 model now so the first utterance doesn't pay
    # the load cost (the weights are baked into the docker image).
    stt_inst.preload()
    proc.userdata["stt"] = stt_inst
    logger.info("[prewarm] VAD + STT ready")


def _build_llm() -> anthropic.LLM:
    """Claude through the JARVIS gateway, authed with a freshly minted
    proxy JWT.

    The gateway routes claude* models via its Anthropic-compatible
    /v1/messages endpoint (it rejects them on /v1/chat/completions), so
    the anthropic plugin is the right client. The anthropic SDK sends the
    credential as `x-api-key`; the hub accepts JWT-shaped x-api-key
    values as bearer credentials (src/cli/src/proxy/server.ts).
    """
    token = proxy_token.mint_from_env(sub="voice-agent")
    base_url = os.environ.get("JARVIS_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")
    model = os.environ.get("VOICE_LLM_MODEL", DEFAULT_LLM_MODEL)
    logger.info("[llm] gateway=%s model=%s (per-job proxy token minted)", base_url, model)
    return anthropic.LLM(model=model, api_key=token, base_url=base_url)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    logger.info("[job] joined room %s", ctx.room.name)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # Non-streaming STT: AgentSession auto-wraps it in a
        # StreamAdapter with the VAD above.
        stt=ctx.proc.userdata["stt"],
        llm=_build_llm(),
        tts=tts_edge.EdgeTTS(
            voice=os.environ.get("VOICE_TTS_VOICE", tts_edge.DEFAULT_VOICE)
        ),
        turn_handling={
            "interruption": {
                "enabled": True,
                # Explicit VAD interruption: auto-detect would probe
                # LiveKit Cloud's adaptive-interruption gateway first,
                # which self-hosted LiveKit doesn't have.
                "mode": "vad",
                # min_words > 0 needs STT partials to fire; our whisper
                # rung is finals-only, so gate on VAD duration alone.
                # The phone's WebRTC AEC keeps agent echo out of the mic.
                "min_duration": 0.55,
                "min_words": 0,
            },
        },
    )

    # ── observability: make each pipeline stage visible in docker logs ──
    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        if ev.is_final:
            logger.info("[stt] user said: %r", ev.transcript)

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) == "assistant":
            logger.info("[llm] agent replied: %r", item.text_content)

    @session.on("agent_state_changed")
    def _on_state(ev) -> None:
        logger.info("[state] %s -> %s", ev.old_state, ev.new_state)

    @session.on("metrics_collected")
    def _on_metrics(ev) -> None:
        metrics.log_metrics(ev.metrics)

    await session.start(
        agent=Agent(instructions=INSTRUCTIONS),
        room=ctx.room,
    )

    greeting = os.environ.get("VOICE_GREETING", "").strip()
    if greeting:
        session.say(greeting)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            # 4 vCPU / 8 GB box shared with the web stack — keep one warm
            # process, don't fan out.
            num_idle_processes=1,
        )
    )
