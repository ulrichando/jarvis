# src/voice-agent/sanitizers/output_language.py
"""Output-language sanitizer — blanks supervisor replies that drift
into a non-Latin script when the user spoke English.

Companion to pipeline/stt_gate.py's INPUT non-Latin gate (added
2026-05-16). The input gate catches background-TV transcripts before
they reach the supervisor; this OUTPUT gate catches the rare case
where a fallback model (DeepSeek v4-pro most notoriously) responds
in a non-Latin script to a Latin-script English input — turn 160 in
the 2026-05-16 telemetry audit returned a Bosnian-formal-letter
reply to a 3-word English fragment.

Trigger conditions (ALL must hold):
  - Accumulated reply contains alphabetic codepoints
  - >30% of those are non-Latin (Cyrillic, Kana, Hanzi, Hangul, etc.)
  - The most recent user turn was NOT in the same script family
    (so a genuine "respond in Japanese" request goes through)

Behavior: blanks the chunk content (TTS emits nothing) and logs at
WARNING. The next turn (user retry) gets a fresh shot at a sane reply.

Install pattern mirrors sanitizers/denial_detector.py exactly:
monkey-patches LLMStream._parse_choice; idempotent via the
_jarvis_output_language_patched flag attribute.

Refs: 2026-05-17 plan §P0-VOICE-2, 2026-05-16 AI review §P0-4.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("jarvis.output_language")

# Activation thresholds (env-overridable for live tuning).
_NON_LATIN_THRESHOLD = float(
    os.environ.get("JARVIS_OUTPUT_NON_LATIN_THRESHOLD", "0.30")
)
# Minimum buffer length before the gate fires — short strings can have
# a single non-Latin char that's 100% of alpha (e.g. "OK 漂亮" mid-typing).
# 12 chars matches the INPUT gate's threshold.
_MIN_BUFFER_LEN = int(
    os.environ.get("JARVIS_OUTPUT_NON_LATIN_MIN_LEN", "12")
)
# Per-stream buffer cap (matches denial_detector's 400).
_BUFFER_CAP = 400


def _non_latin_alpha_ratio(text: str) -> float:
    """Return the fraction of alphabetic codepoints that are NOT Latin
    a-z / A-Z. Returns 0.0 for empty / non-alphabetic text so the
    short-circuit at the call site treats those as "no gate".
    """
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    non_latin = sum(1 for c in alpha if not ("a" <= c.lower() <= "z"))
    return non_latin / len(alpha)


# Script ranges that must never reach TTS for an English/French speaker.
# DeepSeek v4-flash leaks a few of these mixed into otherwise-English replies
# (e.g. "Done 是的"), which sit UNDER the majority-blank threshold — so rather
# than blank the whole (mostly-English) reply, we STRIP these characters and let
# the English through. Latin (incl. French accents é/à/ç) is preserved.
_NON_LATIN_STRIP_RE = re.compile(
    "["
    "　-〿"   # CJK symbols & punctuation (、。「」…)
    "぀-ヿ"   # Hiragana + Katakana
    "㄀-ㄯ"   # Bopomofo
    "㄰-㆏"   # Hangul compatibility Jamo
    "㐀-䶿"   # CJK Unified Ideographs Ext-A
    "一-鿿"   # CJK Unified Ideographs (Hanzi)
    "가-힯"   # Hangul syllables
    "豈-﫿"   # CJK compatibility ideographs
    "Ѐ-ӿ"   # Cyrillic
    "]+"
)


def strip_non_latin_scripts(text: str) -> str:
    """Remove CJK / Kana / Hangul / Cyrillic characters, then collapse any
    doubled space the removal leaves (so "Done 是的 now" → "Done now")."""
    out = _NON_LATIN_STRIP_RE.sub("", text)
    if out != text:
        out = re.sub(r"  +", " ", out)
    return out


def is_non_latin_drift(buffer: str, recent_user_text: str = "") -> bool:
    """Decide whether `buffer` is a non-Latin drift we should suppress.

    Returns False (= pass through) when:
      - buffer too short (less than _MIN_BUFFER_LEN)
      - buffer has no alphabetic content
      - non-Latin alpha ratio <= _NON_LATIN_THRESHOLD
      - the recent user input was ALSO non-Latin (user genuinely
        switched languages — respect that)
    """
    if len(buffer) < _MIN_BUFFER_LEN:
        return False
    out_ratio = _non_latin_alpha_ratio(buffer)
    if out_ratio <= _NON_LATIN_THRESHOLD:
        return False
    # If the user's most recent input was itself majority non-Latin,
    # the model is matching the user's language — don't gate.
    if recent_user_text and _non_latin_alpha_ratio(recent_user_text) > 0.30:
        return False
    return True


def install() -> None:
    """Patch LLMStream._parse_choice to suppress non-Latin drift.

    Idempotent. Re-installation is a no-op.

    The patch runs AFTER the existing _parse_choice patches in the
    chain (deepseek_roundtrip, dsml, pycall, handoff_text,
    denial_detector, internal_phrase) so it sees the post-sanitized
    content. Per-stream buffer accumulates the last 400 chars of
    content; once the buffer hits _MIN_BUFFER_LEN and crosses the
    threshold, every subsequent chunk for that stream gets blanked
    (state is sticky-per-stream so a partial-drift early in the reply
    doesn't escape via later chunks once the buffer drops back).
    """
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_output_language_patched", False):
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    # Per-stream buffer + sticky-trip flag.
    _STREAM_BUFFERS: dict[str, str] = {}
    _STREAM_TRIPPED: set[str] = set()

    def _recent_user_text(self) -> str:
        """Best-effort fetch of the most recent user-role message in
        the stream's chat_ctx. Returns empty string if anything fails
        (the gate then treats user_text as empty → no "user is also
        non-Latin" carve-out applies, but the threshold still gates)."""
        try:
            chat_ctx = getattr(self, "_chat_ctx", None)
            items = getattr(chat_ctx, "items", None) or []
            for item in reversed(items):
                role = getattr(item, "role", None)
                if role == "user":
                    content = getattr(item, "content", None)
                    if isinstance(content, list):
                        return " ".join(str(c) for c in content)[:200]
                    return str(content or "")[:200]
        except Exception:
            pass
        return ""

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        finish = getattr(choice, "finish_reason", None)

        if delta is not None:
            content = getattr(delta, "content", None) or ""
            if content and id is not None:
                if id in _STREAM_TRIPPED:
                    # Sticky-suppress every subsequent chunk for this
                    # stream — once we've decided this reply is drift,
                    # don't let mid-reply Latin recovery sneak through.
                    try:
                        delta.content = ""
                    except Exception:
                        try:
                            object.__setattr__(delta, "content", "")
                        except Exception:
                            pass
                else:
                    user_text = _recent_user_text(self)
                    # Strip stray CJK/Cyrillic chars DeepSeek leaks into otherwise-
                    # English replies (they're under the majority-blank threshold, so
                    # they'd otherwise reach TTS verbatim). Skip ONLY when the user
                    # genuinely spoke a non-Latin language this turn (respect that).
                    if _non_latin_alpha_ratio(user_text) <= _NON_LATIN_THRESHOLD:
                        cleaned = strip_non_latin_scripts(content)
                        if cleaned != content:
                            logger.warning(
                                f"[output-language] stripped non-Latin chars "
                                f"(stream {id[:12] if id else '?'}): "
                                f"{content[:80]!r} -> {cleaned[:80]!r}"
                            )
                            content = cleaned
                            try:
                                delta.content = cleaned
                            except Exception:
                                try:
                                    object.__setattr__(delta, "content", cleaned)
                                except Exception:
                                    pass
                    buf = _STREAM_BUFFERS.get(id, "") + content
                    _STREAM_BUFFERS[id] = buf[-_BUFFER_CAP:]
                    if is_non_latin_drift(buf, user_text):
                        logger.warning(
                            f"[output-language] suppressed non-Latin drift "
                            f"(stream {id[:12] if id else '?'}): "
                            f"ratio={_non_latin_alpha_ratio(buf):.2f} "
                            f"buf={buf[:120]!r}"
                        )
                        _STREAM_TRIPPED.add(id)
                        try:
                            delta.content = ""
                        except Exception:
                            try:
                                object.__setattr__(delta, "content", "")
                            except Exception:
                                pass

        if finish and id is not None:
            _STREAM_BUFFERS.pop(id, None)
            _STREAM_TRIPPED.discard(id)

        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_output_language_patched = True
    # print() not logger.info() — install runs at module-load before
    # the agents framework wires up its structlog formatter, so
    # logger.info messages here would vanish. Matches the DSML +
    # Pycall sanitizers' visibility pattern.
    print(
        f"Output-language sanitizer installed "
        f"(threshold={_NON_LATIN_THRESHOLD:.2f}, min_len={_MIN_BUFFER_LEN})"
    )
