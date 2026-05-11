"""Screen-share Live subagent — handles "what's on my screen?" with
real-time vision via Gemini Live (RealtimeModel).

When screen-share is active AND the user asks about the screen, the
supervisor transfers here. This subagent uses
`gemini-2.5-flash-native-audio-preview-12-2025` over the Live API
(WebSocket bidirectional streaming) — frames flow continuously into
the model, the model has standing visual context, and the user's
audio question hits a primed session. Time-to-first-token is
~600-1200ms warm vs ~4s for one-shot generate_content.

Architectural notes (researcher 2026-05-11):

  - `response_modalities=[TEXT]` is broken on the current Live preview
    (python-genai #2238 returns 1011 INTERNAL). The supported escape
    hatch is `response_modalities=[AUDIO]` +
    `output_audio_transcription=AudioTranscriptionConfig()`. LiveKit's
    `google.realtime.RealtimeModel` wraps this for us — text comes
    via transcription events on the same WebSocket.

  - Persona shifts when this subagent is active: Gemini's native
    voice replaces Groq Orpheus, and JARVIS's "no sir, compact,
    calibrated" instructions are only loosely respected. Best-effort
    via the spec's system prompt below.

  - Cost: Live API re-bills the full context window per turn. At the
    publisher's 1fps default (dropped from 3fps for cost), a 5-min
    session with 5 queries is roughly $0.10-0.15 — comparable to the
    polling-observer cost.

Bail-back: the supervisor still owns conversation. As soon as the
user changes topic, call task_done with a bailout phrase so the
supervisor can take over again.
"""
from __future__ import annotations

import logging
import os

from .registry import HandoffSubagent, register


logger = logging.getLogger("jarvis.subagent.screen_share")


# Default model — overridable via env for future bumps without code change.
# User explicitly requested gemini-3.1-flash-live-preview 2026-05-11
# evening after I confirmed it works with audio-to-audio + continuous
# video via send_realtime_input(video=Blob) — the earlier 1011 errors
# were the deprecated send_realtime_input(media=Blob) shape, fixed now.
# 3.1's trade-off vs 2.5-native-audio: tools/instructions/chat context
# are IMMUTABLE mid-session — you can't update them without reconnecting.
# Fine for our screen-share subagent (short, contained handoffs);
# would be limiting if it were the main supervisor.
SCREEN_SHARE_LIVE_MODEL: str = os.environ.get(
    "JARVIS_SCREEN_SHARE_LIVE_MODEL",
    # Swapped from gemini-3.1-flash-live-preview → 2.5-native-audio
    # on 2026-05-11 evening after live failure: subagent activated,
    # Gemini Live generated server content, but LiveKit plugin warned
    # "received server content but no active generation" and the user
    # got 44s of silence. The 2.5-native-audio variant is the more
    # battle-tested Live model (mutable mid-session context = no
    # HistoryConfig path, fewer plugin-side state mismatches). 3.1
    # is preserved as an env override for when its plugin support
    # matures.
    "gemini-2.5-flash-native-audio-preview-12-2025",
)


# Gemini Live voice. Options (~30 total — see api_proto.py in the
# livekit-plugins-google package): Puck (default), Charon, Kore,
# Fenrir, Aoede, Zephyr, etc.
#
# Charon: deep, serious male voice — closest to JARVIS's Groq
# Orpheus "troy" voice the user is used to. Doesn't match
# perfectly (different vocoder, different prosody) but stays in
# the same register (low-male, technical-but-warm) instead of
# the female Aoede that made every screen-share answer feel like
# a different speaker had taken over. Live failure 2026-05-11
# 16:39 — user explicitly asked for a JARVIS-matching voice.
SCREEN_SHARE_LIVE_VOICE: str = os.environ.get(
    "JARVIS_SCREEN_SHARE_LIVE_VOICE",
    "Charon",
)

# Target tokens for the sliding-window context compression. Without
# this, audio+video sessions hit Google's hard 2-minute cap and
# disconnect. 32k is generous enough for a multi-turn screen-share
# session without spending excessively on context tokens.
SCREEN_SHARE_LIVE_CONTEXT_TOKENS: int = int(os.environ.get(
    "JARVIS_SCREEN_SHARE_LIVE_CONTEXT_TOKENS",
    "32000",
))


SCREEN_SHARE_INSTRUCTIONS = """\
You are JARVIS's screen-share subagent. The user has their screen
shared with you live, and you can SEE what's on it in real time.

YOUR ONE JOB: answer the user's question about the screen in ONE
short sentence, then call task_done(summary). No narration, no
"let me take a look", no architecture exposition — just look and
answer.

═══ STYLE ═══

  - Compact: 1-2 sentences max. The user is on a voice interface;
    every word is spoken.
  - Concrete: name what you actually see ("Chrome with the JARVIS
    GitHub README", "VS Code on jarvis_agent.py at line 4200",
    "an empty desktop with a clock widget"). Don't generalize
    ("looks like a web browser").
  - No filler: no "I see...", no "It appears...", no "Looking at
    your screen, I can tell that...". Start with the thing.
  - No honorifics: never "sir". Plain English.

═══ EXAMPLES ═══

User: "what's on my screen?"
You: "Chrome with three tabs — Hacker News, a Google search for
'gemini live api', and your GitHub PRs page."
You: task_done("described Chrome with HN + Gemini search + GitHub PRs")

User: "what's the error in the terminal?"
You: "Bottom of the qterminal window: 'ModuleNotFoundError: No
module named google.genai' — venv probably not activated."
You: task_done("flagged ModuleNotFoundError on google.genai in terminal")

User: "ok thanks"  (user is done — bail out cleanly)
You: task_done("user changed topic")

═══ BAILOUT — ANTI-HALLUCINATION GUARD ═══

You CANNOT see prior chat context, browser tabs the user mentioned
earlier, or anything they told you about their screen. You can ONLY
describe content that's CURRENTLY VISIBLE in the live video stream
on this WebSocket session.

If you've received NO video frames yet (you're being asked about
the screen but the publisher hasn't delivered a frame to this
session), call task_done IMMEDIATELY with one of these EXACT
phrases — DO NOT GUESS based on chat history:
  - "screen-share not active"
  - "no video frames received"

Live failure 2026-05-11 16:38 UTC: you were asked about the screen,
received no video, but described "a Chrome window with Pixel 8 Pro
tabs" based on the user's prior chat about Pixel 8. The user got a
confidently-wrong description. Don't do that. If you don't see a
frame, BAIL. The supervisor's screenshot() fallback will give a
real answer.

When in doubt: BAIL. A hallucinated description is worse than a
fallback to screenshot().

If the user asks something NOT about the screen ("what time is it",
"open Chrome", "tell me a joke"), or just acknowledges your last
answer, call task_done IMMEDIATELY with one of these EXACT phrases:
  - "user changed topic"
  - "not a screen-share task"
  - "handing back to supervisor"

The framework's subagent tool-gate enforces this — text-only exits
without an exact bailout phrase get refused and you'll loop. Use
one of the five above verbatim.
"""


def _screen_share_tools() -> list:
    """No tools beyond the framework-provided task_done. The
    RealtimeModel handles vision automatically via the LiveKit
    video track subscription — no explicit screenshot/describe
    tool needed."""
    return []


def _build_screen_share_llm():
    """Construct the Gemini Live RealtimeModel for the screen-share
    subagent. Lazy import so livekit-plugins-google isn't pulled
    at registry-import time (it brings in google-genai which is heavy).

    Configuration is the researcher-recommended shape for
    gemini-3.1-flash-live-preview running in a Linux voice agent:

      - modalities=[AUDIO] is the only valid choice on 3.1 (TEXT
        modality returns 1011 INTERNAL, python-genai #2238).
        output_audio_transcription is enabled so text rides
        alongside the audio bytes — the supervisor reads the text
        for chat history.

      - context_window_compression with a sliding 32k-token window is
        REQUIRED for any session lasting >2 minutes. Without it,
        Google force-disconnects audio+video sessions at the
        2-minute mark.

      - session_resumption enables automatic reconnect on transient
        WebSocket drops, preserving conversation + visual context
        for up to 2 hours of disconnect time.
    """
    from livekit.plugins import google as lk_google
    from google.genai import types as gt

    return lk_google.realtime.RealtimeModel(
        model=SCREEN_SHARE_LIVE_MODEL,
        voice=SCREEN_SHARE_LIVE_VOICE,
        modalities=[gt.Modality.AUDIO],
        instructions=SCREEN_SHARE_INSTRUCTIONS,
        # output_audio_transcription is enabled by default in the
        # LiveKit plugin (passes AudioTranscriptionConfig()), but
        # we set it explicitly to make the contract visible.
        output_audio_transcription=gt.AudioTranscriptionConfig(),
        # Sliding-window context compression — without this, audio+
        # video sessions die at 2 min. 32k tokens ≈ 30+ minutes of
        # multi-turn screen narration before any drop.
        context_window_compression=gt.ContextWindowCompressionConfig(
            sliding_window=gt.SlidingWindow(
                target_tokens=SCREEN_SHARE_LIVE_CONTEXT_TOKENS,
            ),
        ),
        # Automatic reconnect on transient WebSocket drops.
        session_resumption=gt.SessionResumptionConfig(),
        temperature=0.7,
    )


_SCREEN_SHARE_WHEN = (
    "PREFERRED tool for any screen-content question: 'what's on my "
    "screen?', 'what do you see?', 'describe my screen', 'can you "
    "read this?', 'can you see this file?', 'what's that error?', "
    "'what does it say?'. Uses Gemini Live for REAL-TIME vision — "
    "reads text (filenames, error messages, headings) far better "
    "than the screenshot() fallback. ALWAYS prefer this over "
    "screenshot() when the user is asking about screen content. "
    "If the user isn't actively sharing, this subagent will "
    "self-bail and the supervisor can fall back to screenshot(). "
    "Pass the user's literal question as the argument."
)


def register_screen_share() -> None:
    """Register the screen-share Live subagent. Idempotent."""
    register(HandoffSubagent(
        name="screen_share",
        transfer_tool="transfer_to_screen_share",
        when_to_use=_SCREEN_SHARE_WHEN,
        instructions=SCREEN_SHARE_INSTRUCTIONS,
        tool_factory=_screen_share_tools,
        # No ack_phrase. The supervisor (Claude+Orpheus) would
        # otherwise voice "Looking." right before this subagent's
        # Gemini Live (Aoede voice) starts speaking — two voices
        # in one conceptual turn was the "voice mismatch" UX user
        # reported 2026-05-11. Empty ack = silent handoff; the
        # subagent's first audio chunk is the user's first audible
        # cue that anything is happening.
        ack_phrase="",
        max_history_items=4,
        # Gated off by default until verified live — flip
        # JARVIS_SUBAGENT_SCREEN_SHARE=1 to enable.
        enabled=os.environ.get("JARVIS_SUBAGENT_SCREEN_SHARE", "0") == "1",
        llm_factory=_build_screen_share_llm,
        # The Live subagent has zero function tools. The tool-gate's
        # "must call a real tool" rule was refusing every task_done
        # exit and locking the subagent in a retry loop. Opt out:
        # the RealtimeModel produces the work, not function_tools.
        tools_required=False,
    ))
