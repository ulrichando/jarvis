"""L2 — gate refusal sets session._jarvis_last_handoff_refused.
Supervisor reads this on the next turn for POST-HANDOFF HONESTY."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_gate_refusal_sets_session_flag():
    """When the gate refuses task_done with 'no real tool', the
    session flag is set to True so the supervisor can hedge."""
    from subagents.agent import _record_handoff_refused
    session = SimpleNamespace()
    _record_handoff_refused(session)
    assert session._jarvis_last_handoff_refused is True


def test_gate_acceptance_clears_session_flag():
    """When a subsequent supervisor tool call succeeds (real
    FunctionCallOutput lands), the flag is cleared."""
    from subagents.agent import _record_handoff_refused, _clear_handoff_refused
    session = SimpleNamespace()
    _record_handoff_refused(session)
    _clear_handoff_refused(session)
    assert getattr(session, "_jarvis_last_handoff_refused", False) is False


def test_flag_clear_is_idempotent():
    """Calling clear on an already-cleared session doesn't raise."""
    from subagents.agent import _clear_handoff_refused
    session = SimpleNamespace()
    _clear_handoff_refused(session)
    assert getattr(session, "_jarvis_last_handoff_refused", False) is False
