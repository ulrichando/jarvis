"""Screen-share Live specialist — handles "what's on my screen?" with
real-time vision via Gemini Live (RealtimeModel).

When screen-share is active AND the user asks about the screen, the
supervisor transfers here. This specialist uses
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

  - Persona shifts when this specialist is active: Gemini's native
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
SCREEN_SHARE_LIVE_MODEL: str = os.environ.get(
    "JARVIS_SCREEN_SHARE_LIVE_MODEL",
    "gemini-2.5-flash-native-audio-preview-12-2025",
)


SCREEN_SHARE_INSTRUCTIONS = """\
You are JARVIS's screen-share specialist. The user has their screen
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

═══ BAILOUT ═══

If you can't see any screen content (the user isn't actually sharing
their screen, or the video stream hasn't started yet), call task_done
IMMEDIATELY with one of these EXACT phrases so the supervisor can
fall back to screenshot():
  - "screen-share not active"
  - "no video frames received"

If the user asks something NOT about the screen ("what time is it",
"open Chrome", "tell me a joke"), or just acknowledges your last
answer, call task_done IMMEDIATELY with one of these EXACT phrases:
  - "user changed topic"
  - "not a screen-share task"
  - "handing back to supervisor"

The framework's specialist tool-gate enforces this — text-only exits
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
    """Construct the Gemini Live RealtimeModel. Lazy import so
    livekit-plugins-google isn't pulled at registry-import time
    (it brings in google-genai which is heavy)."""
    from livekit.plugins import google as lk_google
    return lk_google.realtime.RealtimeModel(
        model=SCREEN_SHARE_LIVE_MODEL,
        modalities=["AUDIO"],  # text path is broken (genai #2238)
        instructions=SCREEN_SHARE_INSTRUCTIONS,
        # output_audio_transcription is enabled by default in the
        # LiveKit plugin so text rides alongside the audio.
    )


_SCREEN_SHARE_WHEN = (
    "PREFERRED tool for any screen-content question: 'what's on my "
    "screen?', 'what do you see?', 'describe my screen', 'can you "
    "read this?', 'can you see this file?', 'what's that error?', "
    "'what does it say?'. Uses Gemini Live for REAL-TIME vision — "
    "reads text (filenames, error messages, headings) far better "
    "than the screenshot() fallback. ALWAYS prefer this over "
    "screenshot() when the user is asking about screen content. "
    "If the user isn't actively sharing, this specialist will "
    "self-bail and the supervisor can fall back to screenshot(). "
    "Pass the user's literal question as the argument."
)


def register_screen_share() -> None:
    """Register the screen-share Live specialist. Idempotent."""
    register(HandoffSubagent(
        name="screen_share",
        transfer_tool="transfer_to_screen_share",
        when_to_use=_SCREEN_SHARE_WHEN,
        instructions=SCREEN_SHARE_INSTRUCTIONS,
        tool_factory=_screen_share_tools,
        ack_phrase="Looking.",
        max_history_items=4,
        # Gated off by default until verified live — flip
        # JARVIS_SUBAGENT_SCREEN_SHARE=1 to enable. The 1011
        # INTERNAL bug on gemini-3.1-flash-live-preview means
        # the underlying API is still flaky on some accounts;
        # we don't want a broken specialist breaking
        # screen-share for users who haven't tested it.
        enabled=os.environ.get("JARVIS_SUBAGENT_SCREEN_SHARE", "0") == "1",
        llm_factory=_build_screen_share_llm,
    ))
