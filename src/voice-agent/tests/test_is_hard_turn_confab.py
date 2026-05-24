"""Tests for is_hard_turn's confab-shape branch (added 2026-05-24).

Backstory: live session AJ_fArDaLyGWFsV (2026-05-24 18:29-18:31 UTC) —
Haiku confabulated "Chrome is open" / "Done — typed anime" with zero
tool calls. The autonomous reviewer never fired because each reply was
short (~45 chars), well below the 400-char long-reply gate. As a
result the self-improve loop never proposed a fix.

is_hard_turn now adds: TASK/REASONING + zero tool calls + strong
completion claim → hard turn. The reviewer fires; the proposal queue
gets a shot at filing a fix."""
from __future__ import annotations

from pipeline.skill_review import TurnSnapshot, is_hard_turn


def _snap(**kw) -> TurnSnapshot:
    base = dict(
        turn_id=0, ts_utc="",
        user_text="",
        jarvis_text="",
        route="TASK_OTHER",
        subagent="",
        computer_use_steps=0,
        tool_call_count=0,
        had_tool_error=False,
    )
    base.update(kw)
    return TurnSnapshot(**base)


def test_confab_short_task_reply_is_hard():
    """Short TASK reply with completion claim + zero tools → hard."""
    snap = _snap(
        route="TASK_OTHER",
        jarvis_text="Chrome is open. I'll navigate to YouTube now.",
        tool_call_count=0,
    )
    assert is_hard_turn(snap) is True


def test_confab_done_typed_short_is_hard():
    """Short reply with 'done — typed X' pattern → hard."""
    snap = _snap(
        route="TASK_OTHER",
        jarvis_text='Done — typed "anime" in the search bar.',
        tool_call_count=0,
    )
    assert is_hard_turn(snap) is True


def test_task_with_tool_call_is_NOT_hard_just_for_claim():
    """If a tool fired, the claim is legitimate (post-tool narration).
    The new branch must NOT trigger when tool_call_count > 0."""
    snap = _snap(
        route="TASK_OTHER",
        jarvis_text="Chrome is open.",
        tool_call_count=1,
    )
    # Falls through to the existing length gate (45 chars < 400) → False.
    assert is_hard_turn(snap) is False


def test_negation_not_hard():
    """'I can't open Chrome' is negation, not a claim — must NOT match."""
    snap = _snap(
        route="TASK_OTHER",
        jarvis_text="I can't open Chrome — no display.",
        tool_call_count=0,
    )
    assert is_hard_turn(snap) is False


def test_banter_short_claim_NOT_hard():
    """The new branch is gated on TASK/REASONING. BANTER never triggers."""
    snap = _snap(
        route="BANTER",
        jarvis_text="Chrome is open.",
        tool_call_count=0,
    )
    assert is_hard_turn(snap) is False


def test_emotional_short_claim_NOT_hard():
    """EMOTIONAL routes never hit the new branch."""
    snap = _snap(
        route="EMOTIONAL",
        jarvis_text="Done.",
        tool_call_count=0,
    )
    assert is_hard_turn(snap) is False


def test_existing_long_reply_branch_still_works():
    """Pre-existing length gate untouched: TASK + 400+ chars → hard."""
    snap = _snap(
        route="TASK_OTHER",
        jarvis_text="x" * 500,
        tool_call_count=2,
    )
    assert is_hard_turn(snap) is True


def test_existing_computer_use_branch_still_works():
    """Pre-existing computer_use_steps gate untouched."""
    snap = _snap(
        route="TASK_OTHER",
        jarvis_text="ok",
        computer_use_steps=5,
        tool_call_count=0,
    )
    assert is_hard_turn(snap) is True


def test_empty_text_not_hard():
    """Empty jarvis_text never trips any branch."""
    snap = _snap(route="TASK_OTHER", jarvis_text="", tool_call_count=0)
    assert is_hard_turn(snap) is False
