"""Barge-in truncation — TTS position-table accounting + heard-portion cut.

When the user interrupts mid-reply, the assistant's chat_ctx must be
updated to reflect ONLY the audio that actually played — not the full
text that was generated. Otherwise the next turn's LLM sees a
"completed" claim that the user never heard, and the conversation
desyncs (asks the user about content they were never told).

The mechanism:

  1. `LoggingGroqChunkedStream._run` calls `record_synthesis()` after
     each completed synthesize() call, appending (cumulative_ms,
     cumulative_chars) to the session's `_jarvis_tts_position_table`.

  2. The agent_state_changed handler accumulates "speaking"-segment
     durations into `_jarvis_agent_audio_ms_acc`.

  3. On barge-in (user_state→speaking while agent_state==speaking),
     `truncate_to_heard_portion` walks the position table to find the
     last entry where cumulative_ms ≤ audio_end_ms, then cuts the
     item's text to that character count and mutates `item.content`
     in place.

Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md

Hoisted from `jarvis_agent.py` 2026-05-10 (Step 9 of the audit —
tests/test_voice_fixes_2026_05_04 + test_pycall_sanitizer were
reaching into jarvis_agent for the underscored helpers).
"""
from __future__ import annotations


__all__ = [
    "GROQ_ORPHEUS_BYTES_PER_MS",
    "flatten_chat_content",
    "record_synthesis",
    "truncate_to_heard_portion",
]


# Groq Orpheus output is 48 kHz mono 16-bit WAV → 48000 × 1 × 2 = 96 bytes/ms.
# The 44-byte WAV header rounds to <1 ms — ignored.
GROQ_ORPHEUS_BYTES_PER_MS: int = 96


def flatten_chat_content(content: object) -> str:
    """ChatMessage.content can be a string, a list of mixed parts
    (strings + ImageContent + etc), or None. Flatten to a plain
    string — the DB only stores text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            else:
                # Non-string content (images, tool calls). Skip —
                # don't pollute the transcript.
                continue
        return " ".join(parts).strip()
    return str(content)


def record_synthesis(session, input_chars: int, audio_bytes: int) -> None:
    """Append one entry to the session's TTS position table after a
    completed synthesize() call. Idempotent; tolerant of missing
    session or missing attr."""
    if session is None:
        return
    table = getattr(session, "_jarvis_tts_position_table", None)
    if table is None:
        table = []
        session._jarvis_tts_position_table = table
    audio_ms = audio_bytes // GROQ_ORPHEUS_BYTES_PER_MS
    if table:
        prev_ms, prev_chars = table[-1]
    else:
        prev_ms, prev_chars = 0, 0
    table.append((prev_ms + audio_ms, prev_chars + input_chars))


def truncate_to_heard_portion(item, position_table, audio_end_ms):
    """Cut an assistant turn's text to only the audio that played.

    Used by the barge-in truncation gate in `_on_item`. When the user
    interrupts mid-reply, this returns the heard portion of `item.content`
    and mutates `item.content` in place so chat_ctx for the next turn
    reflects only what was heard. Matches OpenAI Realtime's
    `conversation.item.truncate(audio_end_ms=N)` semantic.

    Args:
        item: livekit-agents chat-ctx item with `.content` (str or [str]).
        position_table: list of (cumulative_ms, cumulative_chars) tuples,
            one entry per synthesize() call in this assistant turn.
        audio_end_ms: ms of audio actually heard (= _jarvis_agent_audio_ms_acc).

    Returns:
        (truncated_text: str, mutated: bool). `mutated` is True iff
        item.content was rewritten to a strictly shorter form.
    """
    full_text = flatten_chat_content(getattr(item, "content", None)) or ""
    if not position_table:
        return full_text, False

    # Walk to the last entry whose cumulative_ms ≤ audio_end_ms.
    cut_chars = 0
    for cum_ms, cum_chars in position_table:
        if cum_ms <= audio_end_ms:
            cut_chars = cum_chars
        else:
            break

    if cut_chars >= len(full_text):
        # User heard everything (or position table over-reports).
        return full_text, False

    truncated = full_text[:cut_chars]
    # Mutate in place so chat_ctx reflects heard-only on next LLM turn.
    if isinstance(item.content, list):
        item.content = [truncated]
    else:
        item.content = truncated
    return truncated, True
