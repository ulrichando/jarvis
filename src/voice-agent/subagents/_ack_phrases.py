"""Central registry for subagent `ack_phrase` strings.

The `ack_phrase` is the only supervisor-side voice the user hears
between the handoff and the subagent's `task_done` summary. Per
spec, it should sit in the peer-engineer register (no honorifics,
no archaic verbs).

Centralizing here for two reasons:
  1. Register-consistency tweaks become 1-file edits instead of 9.
  2. Future canned-WAV rendering (docs/superpowers/specs/
     2026-05-04-jarvis-voice-resilience-design.md) can pre-render
     these into `~/.jarvis/cache/voice/` and play them with zero
     TTS latency on handoff.

Each name follows the pattern `ACK_{SUBAGENT_NAME}`. Values must
remain ≤4 words to keep the handoff transition snappy.
"""
from __future__ import annotations

# HandoffSubagent (transfer_to_X) phrases
ACK_DESKTOP        = "Right away."
ACK_BROWSER        = "On it."

# DelegatedSubagent (delegate role=X) phrases
ACK_SUMMARIZE      = "One sec."
ACK_WEATHER        = "Checking."
ACK_RESEARCHER     = "Looking into it."
ACK_VALIDATOR      = "Verifying."
ACK_CODE_REVIEWER  = "Reviewing."
ACK_MEMORY_RECALL  = "Looking it up."
ACK_GITHUB         = "Looking it up."


# Convenience for tests / callers that want to assert on the full set.
ALL_ACK_PHRASES: dict[str, str] = {
    "desktop":       ACK_DESKTOP,
    "browser":       ACK_BROWSER,
    "summarize":     ACK_SUMMARIZE,
    "weather":       ACK_WEATHER,
    "researcher":    ACK_RESEARCHER,
    "validator":     ACK_VALIDATOR,
    "code_reviewer": ACK_CODE_REVIEWER,
    "memory_recall": ACK_MEMORY_RECALL,
    "github":        ACK_GITHUB,
}
