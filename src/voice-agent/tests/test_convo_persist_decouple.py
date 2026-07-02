"""Tests for the dispatcher-independent conversation-persistence sequence.

Live incident 2026-07-01: conversations.db stopped recording messages at
04:58Z while telemetry kept flowing. Root cause: the `_on_item` persist
block was gated on `_jarvis_turn_count > 0`, but every increment site
lives in the dispatcher's swap paths (turn_dispatcher.py) / the graph
prefix node (turn_graph.py). With a tray-pinned model +
JARVIS_PIN_ALL_ROUTES=1 (the billing-day mitigation, .env 05:31Z) the
dispatcher is skipped entirely — count stuck at 0, zero messages, while
sessions/auto-title/telemetry all kept working. `_jarvis_turn_user_text`
is likewise dispatcher-stashed only.

The fix: `_convo_turn_seq` / `_convo_user_text` read BOTH the dispatcher
state and a dispatcher-independent stash (`_jarvis_convo_seq` /
`_jarvis_convo_user_text`, bumped on user items in `_on_item` itself).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

# Add voice-agent dir to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_agent


class TestConvoTurnSeq:
    def test_dispatcher_alive_uses_turn_count(self):
        s = SimpleNamespace(_jarvis_turn_count=5)
        assert jarvis_agent._convo_turn_seq(s) == 5

    def test_dispatcher_dead_uses_convo_seq(self):
        # The live failure: pin-all-routes → dispatcher skipped → count 0.
        s = SimpleNamespace(_jarvis_turn_count=0, _jarvis_convo_seq=3)
        assert jarvis_agent._convo_turn_seq(s) == 3

    def test_both_zero_keeps_gate_closed(self):
        s = SimpleNamespace()
        assert jarvis_agent._convo_turn_seq(s) == 0

    def test_max_wins_when_sources_disagree(self):
        # Skipped replies advance convo_seq past turn_count — max keeps
        # the sequence monotonic so UNIQUE(session, role, seq) never
        # silently drops a later turn as a "duplicate".
        s = SimpleNamespace(_jarvis_turn_count=2, _jarvis_convo_seq=4)
        assert jarvis_agent._convo_turn_seq(s) == 4
        s = SimpleNamespace(_jarvis_turn_count=7, _jarvis_convo_seq=4)
        assert jarvis_agent._convo_turn_seq(s) == 7

    def test_none_values_are_safe(self):
        s = SimpleNamespace(_jarvis_turn_count=None, _jarvis_convo_seq=None)
        assert jarvis_agent._convo_turn_seq(s) == 0


class TestConvoUserText:
    def test_dispatcher_stash_wins(self):
        s = SimpleNamespace(
            _jarvis_turn_user_text="raw transcript",
            _jarvis_convo_user_text="item text",
        )
        assert jarvis_agent._convo_user_text(s) == "raw transcript"

    def test_falls_back_to_item_stash(self):
        s = SimpleNamespace(_jarvis_turn_user_text="", _jarvis_convo_user_text="item text")
        assert jarvis_agent._convo_user_text(s) == "item text"

    def test_both_missing_returns_empty(self):
        assert jarvis_agent._convo_user_text(SimpleNamespace()) == ""
