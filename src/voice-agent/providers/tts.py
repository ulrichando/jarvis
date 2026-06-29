"""TTS chain + per-route dispatcher build.

On-device Kokoro/Piper is the TTS primary (JARVIS_LOCAL_TTS_PRIMARY /
JARVIS_LOCAL_TTS_ONLY — the default config); Microsoft Edge-TTS
(auth-free) is the network fallback. `build_tts_chain` returns the
ordered engine list for a FallbackAdapter; `build_dispatching_tts`
returns the per-route DispatchingTTS the AgentSession uses.

History: Groq Orpheus (the `LoggingGroqTTS` / `LoggingGroqChunkedStream`
shims) was removed 2026-06-29 in the full-Groq-eradication pass;
ElevenLabs was removed 2026-05-01. NOTE: the Orpheus stream was the only
caller of `record_synthesis` (barge-in position table) and
`speaking_tracker.note_speaking` (echo-aware barge-in) — both have been
dormant since TTS went Kokoro-primary (2026-06-22) and are NOT re-homed
onto the Kokoro/Edge path here (a separate task if echo-aware barge-in
is wanted back).
"""
from __future__ import annotations

import logging
import os

from livekit.agents import tts
from providers import edge_tts as edge_tts_plugin
from providers.piper_tts import build_local_tts

from pipeline.dispatching_tts import DispatchingTTS
from pipeline.settings import read_unified_setting


logger = logging.getLogger("jarvis.tts")


__all__ = [
    "build_tts_chain",
    "build_dispatching_tts",
]


# ── TTS chain + dispatcher build ─────────────────────────────────────

def build_tts_chain(tts_provider_file) -> list:
    """Build the ordered TTS list for FallbackAdapter.

    Priority (first wins):
      1. ~/.jarvis/tts-provider file — written by the tray's Voice submenu
         (a "kokoro:<voice>" or "edge:<voice>" spec prefix).
      2. Default: on-device Kokoro/Piper when available, else Edge-TTS.
    Edge-TTS (no auth, always available) + the local engine are appended
    as fallback rungs.

    Groq Orpheus was removed 2026-06-29 (full-Groq-eradication pass); a
    stale "groq:" spec now falls through to the default. ElevenLabs was
    removed 2026-05-01.

    `tts_provider_file` is the Path to the legacy flat file written by the
    tray, passed in so this module doesn't reach back into jarvis_agent.
    """
    edge_voice = os.getenv("JARVIS_EDGE_VOICE", "en-US-GuyNeural")
    local_only = os.environ.get("JARVIS_LOCAL_TTS_ONLY", "0") == "1"

    provider, voice = "", ""
    spec = read_unified_setting("tts-provider", tts_provider_file)
    if spec and ":" in spec:
        provider, voice = (s.strip() for s in spec.split(":", 1))

    # The on-device rung (Kokoro/Piper). build_local_tts() reads its voice
    # from JARVIS_LOCAL_TTS_VOICE, kept in sync with the tray's Kokoro pick.
    local = build_local_tts()

    def _edge(v):
        return edge_tts_plugin.EdgeTTS(voice=v or edge_voice)

    # Strict-local: on-device only, no cloud rungs (mirrors the dispatcher's
    # JARVIS_LOCAL_TTS_ONLY). A local-engine failure then has no fallback.
    if local_only and local is not None:
        logger.info("[tts] JARVIS_LOCAL_TTS_ONLY=1 — on-device TTS only (no cloud fallback)")
        return [local]

    # Spec prefix picks the PRIMARY engine.
    primary, primary_engine = None, None
    if provider == "kokoro" and local is not None:
        primary, primary_engine = local, "kokoro"
        logger.info(f"[tts] Kokoro on-device primary [tray selection: {spec}]")
    elif provider == "edge":
        primary, primary_engine = _edge(voice), "edge"
        logger.info(f"[tts] Edge-TTS voice={voice or edge_voice} primary [tray selection]")

    if primary is None:
        # No usable engine pick → on-device when available, else Edge.
        if local is not None:
            primary, primary_engine = local, "kokoro"
            logger.info("[tts] Kokoro on-device primary [default]")
        else:
            primary, primary_engine = _edge(edge_voice), "edge"
            logger.info(f"[tts] Edge-TTS voice={edge_voice} primary [default — no local TTS]")

    # Append the OTHER engines as fallback rungs (resilience), skipping the
    # one already primary. Order: Edge → local (offline).
    chain = [primary]
    if primary_engine != "edge":
        try:
            chain.append(_edge(edge_voice))
        except Exception as e:
            logger.warning(f"[tts] Edge fallback unavailable ({e})")
    if primary_engine != "kokoro" and local is not None:
        chain.append(local)
    return chain


def build_dispatching_tts() -> DispatchingTTS:
    """Per-route TTS dispatcher. On-device Kokoro/Piper is the primary
    (JARVIS_LOCAL_TTS_PRIMARY / JARVIS_LOCAL_TTS_ONLY — the default
    config); Edge-TTS (auth-free) is the network fallback. Every route
    shares the same local→Edge chain shape.

    Groq Orpheus + its per-route voices were removed 2026-06-29
    (full-Groq-eradication pass); ElevenLabs was removed 2026-05-01. The
    Orpheus stream was the only caller of record_synthesis (barge-in
    position table) and speaking_tracker.note_speaking (echo-aware
    barge-in) — both dormant since TTS went Kokoro-primary 2026-06-22 and
    NOT re-homed here.
    """
    edge_voice = os.environ.get("JARVIS_EDGE_VOICE", "en-US-ChristopherNeural")
    try:
        _edge_fallback = edge_tts_plugin.EdgeTTS(voice=edge_voice)
        _edge_fallback.voice_id = f"edge:{edge_voice[:10]}…"
    except Exception as e:
        logger.warning(f"[dispatch] edge_tts construction failed ({e}); routes may have no fallback")
        _edge_fallback = None

    # Offline rung shared across every route. None unless JARVIS_LOCAL_TTS_ENABLED=1.
    _local_fallback = build_local_tts()
    if _local_fallback is not None:
        _engine = os.environ.get("JARVIS_LOCAL_TTS_ENGINE", "piper").strip() or "piper"
        _local_fallback.voice_id = f"{_engine}:local"

    _local_only = (
        os.environ.get("JARVIS_LOCAL_TTS_ONLY", "0") == "1"
        and _local_fallback is not None
    )
    # Without Orpheus the only non-local engine is Edge, so "local primary"
    # just means putting the local rung first. ONLY implies primary.
    _local_primary = _local_only or (
        os.environ.get("JARVIS_LOCAL_TTS_PRIMARY", "0") == "1"
        and _local_fallback is not None
    )
    if _local_only:
        _only_engine = os.environ.get("JARVIS_LOCAL_TTS_ENGINE", "piper").strip() or "piper"
        logger.warning(
            "[dispatch] JARVIS_LOCAL_TTS_ONLY=1 — Edge fallback dropped; "
            "on-device %s only. A local-engine failure has NO fallback by design.",
            _only_engine,
        )
    elif _local_primary:
        logger.info("[dispatch] local TTS primary on all routes; Edge-TTS is the fallback")
    else:
        logger.info("[dispatch] Edge-TTS primary on all routes")

    def _route_chain():
        """Build one route's TTS chain. Strict-local → bare local engine.
        Otherwise a FallbackAdapter (local→Edge when local-primary, else
        Edge→local) that auto-wraps the non-streaming local TTS in
        StreamAdapter for per-sentence synthesis."""
        if _local_only:
            return _local_fallback
        if _local_primary:
            rungs = [r for r in (_local_fallback, _edge_fallback) if r is not None]
        else:
            rungs = [r for r in (_edge_fallback, _local_fallback) if r is not None]
        if not rungs:
            raise RuntimeError(
                "build_dispatching_tts: no TTS engine available — Edge-TTS "
                "construction failed and no local TTS built"
            )
        if len(rungs) == 1:
            return rungs[0]
        try:
            wrapped = tts.FallbackAdapter(rungs)
            wrapped.voice_id = getattr(rungs[0], "voice_id", "?")
            return wrapped
        except Exception as e:
            logger.warning(f"[dispatch] FallbackAdapter wrap failed ({e}); using first rung alone")
            return rungs[0]

    inners: dict[str, object] = {
        route: _route_chain() for route in ("BANTER", "TASK", "REASONING", "EMOTIONAL")
    }
    fallback = inners["TASK"]

    # French inner — EdgeTTS French voice; dropped under strict-local (the
    # 'fr' pick then falls back to the on-device English chain).
    if _local_only:
        _fr_inner = None
    else:
        fr_voice = os.environ.get("JARVIS_FR_EDGE_VOICE", "fr-FR-HenriNeural")
        try:
            _fr_inner = edge_tts_plugin.EdgeTTS(voice=fr_voice)
            _fr_inner.voice_id = f"edge:{fr_voice[:18]}…"
        except Exception as e:
            logger.warning(
                f"[dispatch] French edge_tts construction failed ({e}); "
                f"fr will fall back to English chain"
            )
            _fr_inner = None

    return DispatchingTTS(inners=inners, fallback=fallback, fr_inner=_fr_inner)
