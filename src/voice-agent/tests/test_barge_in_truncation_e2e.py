"""End-to-end test for the barge-in truncation flow.

Constructs a fake session + item + position table, simulates the
`conversation_item_added` event firing, and verifies:
- item.content is mutated to the heard portion
- the saved-text variable equals the truncated form
- a non-interrupted turn is left untouched
- empty position table is graceful no-op

Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
"""
from __future__ import annotations
from types import SimpleNamespace

from jarvis_agent import _truncate_to_heard_portion


def _build_session(table, audio_end_ms, interrupted):
    return SimpleNamespace(
        _jarvis_tts_position_table=table,
        _jarvis_agent_audio_ms_acc=audio_end_ms,
        _jarvis_was_interrupted=interrupted,
    )


def _simulate_gate(session, item, role):
    """Mirror the inline truncation gate in `_on_item` so the test
    pins the exact integration shape. If the production gate's logic
    changes, this helper must be updated to match — that's the
    coupling we want to catch with this test."""
    from jarvis_agent import _flatten_chat_content
    text = _flatten_chat_content(getattr(item, "content", None))
    if role == "assistant" and getattr(session, "_jarvis_was_interrupted", False):
        audio_end_ms = getattr(session, "_jarvis_agent_audio_ms_acc", 0) or 0
        table = getattr(session, "_jarvis_tts_position_table", None) or []
        truncated, mutated = _truncate_to_heard_portion(item, table, audio_end_ms)
        if mutated:
            text = truncated
    return text


class TestBargeInE2E:
    def test_interrupted_turn_truncated_in_both_item_and_text(self):
        # 3 synthesize calls for "Hello sir, " "I'm here " "to assist."
        # cumulative: (96ms, 11) (192ms, 20) (288ms, 30)
        # Interrupt heard 200ms — last full chunk boundary is 192ms (chunk 2).
        item = SimpleNamespace(content="Hello sir, I'm here to assist.")
        sess = _build_session(
            table=[(96, 11), (192, 20), (288, 30)],
            audio_end_ms=200,
            interrupted=True,
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        # Both item.content (chat_ctx) and saved_text must equal heard portion.
        assert item.content == "Hello sir, I'm here "
        assert saved_text == "Hello sir, I'm here "

    def test_non_interrupted_turn_left_unchanged(self):
        item = SimpleNamespace(content="Hello sir, I'm here to assist.")
        sess = _build_session(
            table=[(96, 11), (192, 20), (288, 30)],
            audio_end_ms=288,
            interrupted=False,  # no interrupt → gate is a no-op
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        assert item.content == "Hello sir, I'm here to assist."
        assert saved_text == "Hello sir, I'm here to assist."

    def test_user_role_left_unchanged_even_if_interrupted_flag(self):
        # The interrupted flag applies to assistant turns; user turns
        # must never be truncated.
        item = SimpleNamespace(content="Hello, what's the time?")
        sess = _build_session(
            table=[(96, 6)], audio_end_ms=50, interrupted=True
        )
        saved_text = _simulate_gate(sess, item, role="user")
        assert item.content == "Hello, what's the time?"
        assert saved_text == "Hello, what's the time?"

    def test_empty_position_table_graceful_no_op_even_if_interrupted(self):
        # Dispatcher routed to a TTS without our wrapper → no entries.
        # Gate must not crash; existing text is preserved.
        item = SimpleNamespace(content="Hello.")
        sess = _build_session(table=[], audio_end_ms=100, interrupted=True)
        saved_text = _simulate_gate(sess, item, role="assistant")
        assert item.content == "Hello."
        assert saved_text == "Hello."

    def test_audio_end_ms_zero_records_empty_string(self):
        item = SimpleNamespace(content="Hello sir, I'm here to assist.")
        sess = _build_session(
            table=[(96, 11)], audio_end_ms=0, interrupted=True
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        assert item.content == ""
        assert saved_text == ""

    def test_late_interrupt_past_total_audio_returns_full(self):
        # Hangover case — user spoke after TTS naturally ended.
        # interrupted=True but audio_end_ms exceeds total_ms.
        item = SimpleNamespace(content="Hello sir, I'm here.")
        sess = _build_session(
            table=[(96, 11), (192, 20)], audio_end_ms=500, interrupted=True
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        # cut_chars = 20 == len(text), helper returns mutated=False.
        assert item.content == "Hello sir, I'm here."
        assert saved_text == "Hello sir, I'm here."
