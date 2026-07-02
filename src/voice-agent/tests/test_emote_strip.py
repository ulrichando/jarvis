"""Tests for strip_emote_markup — the TTS transform that drops markdown
stage-direction emotes and letterless replies.

Live capture 2026-07-01 20:52–21:05 UTC: deepseek-v4-flash prefixed
replies with `*(chuckles)*`-style emotes; Kokoro received a bare `*`,
pushed zero audio frames, the FallbackAdapter marked it unavailable and
flipped the voice to EdgeTTS, and only the `*(` husk got committed to
chat_ctx — which then taught the model to emit more emotes. This filter
guarantees TTS never sees a letterless segment.
"""
import asyncio
import sys
from pathlib import Path

# Add voice-agent dir to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_agent


def run_filter(text_in: str):
    """Drive the async transform with a single-chunk stream; None = dropped."""
    async def _gen():
        yield text_in

    async def _collect():
        out = []
        async for chunk in jarvis_agent.strip_emote_markup(_gen()):
            out.append(chunk)
        return "".join(out) if out else None

    return asyncio.run(_collect())


class TestEmoteStrip:
    def test_leading_emote_dropped_text_kept(self):
        assert run_filter("*(chuckles)* Hello.") == "Hello."

    def test_emote_only_reply_dropped(self):
        assert run_filter("*(soft laugh)") is None

    def test_bare_asterisk_dropped(self):
        # The exact segment that zero-framed Kokoro live 2026-07-01.
        assert run_filter("*") is None

    def test_truncation_husk_dropped(self):
        # The `*(` shape that got committed to chat_ctx live.
        assert run_filter("*(") is None

    def test_mid_sentence_emote_removed(self):
        assert run_filter("Sure — *(nods)* done.") == "Sure — done."

    def test_plain_text_untouched(self):
        assert run_filter("It's 9:42.") == "It's 9:42."

    def test_plain_parenthetical_preserved(self):
        assert run_filter("That's about (sixty) euros.") == "That's about (sixty) euros."

    def test_emphasis_keeps_word(self):
        # Bare *emphasis* is not a stage direction — keep the word.
        assert run_filter("that was *really* fast") == "that was really fast"

    def test_bold_markdown_stripped(self):
        assert run_filter("**Done.** All set.") == "Done. All set."

    def test_non_latin_text_speakable(self):
        # Speakable check must accept any script, not just ASCII.
        assert run_filter("Хорошо.") == "Хорошо."

    def test_empty_stream_dropped(self):
        assert run_filter("") is None

    def test_punctuation_only_dropped(self):
        assert run_filter("... !!") is None
