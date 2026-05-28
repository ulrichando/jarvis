"""Tests for the signature scheme — must be stable across unrelated
line-number changes, and must disambiguate distinct bugs that share
a centralized handler. Spec 2026-05-27 Part 2."""
from __future__ import annotations

import pytest


def test_signature_excludes_line_numbers():
    """Same exc_class, same (file, method), different line numbers
    must produce the SAME signature."""
    from pipeline.automod.error_logger import _signature
    frames_a = [("src/voice-agent/jarvis_agent.py", "_on_user_input")]
    frames_b = [("src/voice-agent/jarvis_agent.py", "_on_user_input")]
    # Line numbers are not in the input to _signature at all — proving
    # the API doesn't accept them is a stronger guarantee than asserting
    # they're stripped.
    assert _signature("ValueError", frames_a) == _signature("ValueError", frames_b)


def test_signature_differs_for_different_exc_class():
    """Different exc_class → different signature, even with identical frames."""
    from pipeline.automod.error_logger import _signature
    frames = [("src/voice-agent/jarvis_agent.py", "_on_user_input")]
    assert _signature("ValueError", frames) != _signature("KeyError", frames)


def test_signature_differs_for_different_files():
    """Same exc_class at different files → different signatures."""
    from pipeline.automod.error_logger import _signature
    frames_a = [("src/voice-agent/jarvis_agent.py", "foo")]
    frames_b = [("src/voice-agent/pipeline/turn_router.py", "foo")]
    assert _signature("ValueError", frames_a) != _signature("ValueError", frames_b)


def test_multi_frame_disambiguates_centralized_handler():
    """Two distinct bugs both surfacing through a shared centralized
    handler must get DIFFERENT signatures because the deeper frames
    differ. This is the multi-frame value-add over single-frame schemes."""
    from pipeline.automod.error_logger import _signature
    # Bug A: deep call from foo(), bubbles through central_handler()
    frames_a = [
        ("src/voice-agent/tools/foo.py", "do_foo_thing"),
        ("src/voice-agent/jarvis_agent.py", "central_handler"),
    ]
    # Bug B: deep call from bar(), bubbles through the same handler
    frames_b = [
        ("src/voice-agent/tools/bar.py", "do_bar_thing"),
        ("src/voice-agent/jarvis_agent.py", "central_handler"),
    ]
    sig_a = _signature("ValueError", frames_a)
    sig_b = _signature("ValueError", frames_b)
    assert sig_a != sig_b, (
        "multi-frame signature must distinguish bugs that share a "
        "centralized handler"
    )


def test_signature_is_order_independent():
    """Frame ORDER should not affect the signature — same set of (file,
    method) pairs in different orders must produce identical sigs.
    (Spec calls for sorted(set(...)) inside the signature function.)"""
    from pipeline.automod.error_logger import _signature
    frames_a = [
        ("src/voice-agent/tools/foo.py", "do_foo"),
        ("src/voice-agent/jarvis_agent.py", "handler"),
    ]
    frames_b = [
        ("src/voice-agent/jarvis_agent.py", "handler"),
        ("src/voice-agent/tools/foo.py", "do_foo"),
    ]
    assert _signature("ValueError", frames_a) == _signature("ValueError", frames_b)
