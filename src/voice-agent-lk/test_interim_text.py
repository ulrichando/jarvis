"""Self-check for the interim-transcript revision logic (interim_text.py).

Pure python + asserts, no frameworks, no livekit, no models, no audio —
runnable anywhere:  python3 test_interim_text.py

Simulates the sequence of whisper hypotheses an interim decoder produces
on a growing utterance and proves that:
  - the display self-revises Claude-style ("wut" -> "what" -> "what's the")
  - words that agree across two consecutive hypotheses commit and never
    flicker afterwards (LocalAgreement-2 stability)
  - a transiently shrunken hypothesis never regresses the display
  - window trimming picks segment boundaries and freezes the right text
"""
from __future__ import annotations

from interim_text import LocalAgreement, compose, freeze_text, pick_cut_time, plan_trim


def test_revision_and_commit() -> None:
    la = LocalAgreement()
    # tick 1: whisper's first guess on 0.5s of audio
    assert la.update(["wut"]) == "wut"
    # tick 2: more audio, the word is revised — nothing had committed yet
    assert la.update(["what"]) == "what"
    assert la.committed_text() == ""  # "wut" vs "what" never agreed
    # tick 3: hypothesis grows
    assert la.update(["what's", "the"]) == "what's the"
    # tick 4: "what's the" agreed twice -> commits; tail is interim
    assert la.update(["what's", "the", "weather"]) == "what's the weather"
    assert la.committed_text() == "what's the"
    # tick 5: later hypothesis re-cases the committed word — committed
    # surface stays frozen (no flicker), new words keep flowing
    out = la.update(["What's", "the", "weather", "today"])
    assert out == "what's the weather today"
    assert la.committed_text() == "what's the weather"


def test_no_regression_on_shrunken_hypothesis() -> None:
    la = LocalAgreement()
    la.update(["good", "morning", "jarvis"])
    la.update(["good", "morning", "jarvis"])  # commits all three
    assert la.committed_text() == "good morning jarvis"
    # decoder wobble: a shorter hypothesis must not shrink the display
    assert la.update(["good"]) == "good morning jarvis"
    # and must not seed a bogus agreement on the next tick
    assert la.update(["good", "morning", "jarvis", "set", "a"]) == (
        "good morning jarvis set a"
    )
    assert la.committed_text() == "good morning jarvis"  # "set a" seen once


def test_commit_uses_newer_surface_form() -> None:
    la = LocalAgreement()
    la.update(["hello", "world"])
    # agreement is punctuation/case-insensitive; the NEWER surface commits,
    # so late punctuation fixes land before the word freezes
    assert la.update(["Hello,", "world!"]) == "Hello, world!"
    assert la.committed_text() == "Hello, world!"


def test_committed_prefix_is_monotonic() -> None:
    """Feed a realistic self-revising sequence; whatever was committed at
    tick N must be a prefix of the display at every tick > N."""
    seq = [
        ["turn"],
        ["turn", "on"],
        ["turn", "on", "the"],
        ["turn", "on", "the", "lights"],
        ["turn", "on", "the", "lights", "in"],
        ["turn", "on", "the", "lights", "in", "the", "kitchen"],
        ["turn", "on", "the", "lights", "in", "the", "kitchen."],
    ]
    la = LocalAgreement()
    prev_committed = ""
    for words in seq:
        display = la.update(words)
        assert display.startswith(prev_committed), (prev_committed, display)
        committed = la.committed_text()
        assert committed.startswith(prev_committed), (prev_committed, committed)
        prev_committed = committed
    # the last two hypotheses agree everywhere (kitchen ~ kitchen. after
    # normalization), so the whole sentence ends committed
    assert la.committed_text() == "turn on the lights in the kitchen."


def test_pick_cut_time() -> None:
    segs = [(0.0, 2.4, "a"), (2.4, 5.1, "b"), (5.1, 8.0, "c")]
    # nearest segment end within tolerance wins
    assert pick_cut_time(segs, 5.0, tolerance=1.5) == 5.1
    assert pick_cut_time(segs, 2.0, tolerance=1.5) == 2.4
    # no boundary near -> fall back to the raw target
    assert pick_cut_time(segs, 1.0, tolerance=0.5) == 1.0
    assert pick_cut_time([], 7.0) == 7.0


def test_freeze_text_and_plan_trim() -> None:
    segs = [(0.0, 2.4, "set a timer"), (2.4, 5.1, "for ten minutes"), (5.1, 8.0, "and remind")]
    assert freeze_text(segs, 5.1) == "set a timer for ten minutes"
    assert freeze_text(segs, 2.4) == "set a timer"
    assert freeze_text(segs, 0.1) == ""

    # buffer fits the window -> no trim
    assert plan_trim(segs, 0.0, 8.0, window=10.0, keep_recent=6.0) is None

    # 12s buffered, 10s window, keep 6s -> target cut 6.0, snaps to seg end 5.1
    plan = plan_trim(segs, 0.0, 12.0, window=10.0, keep_recent=6.0)
    assert plan is not None
    cut, frozen = plan
    assert cut == 5.1
    assert frozen == "set a timer for ten minutes"

    # no segments at all (decoder lagging) -> raw target cut, nothing frozen
    plan = plan_trim([], 0.0, 12.0, window=10.0, keep_recent=6.0)
    assert plan == (6.0, "")

    # cut never lands before the buffer head
    plan = plan_trim(segs, 7.0, 18.0, window=10.0, keep_recent=6.0)
    assert plan is not None and plan[0] >= 7.0


def test_compose() -> None:
    assert compose("", "b") == "b"
    assert compose("a", "") == "a"
    assert compose("a", "b") == "a b"
    assert compose("", "") == ""


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} checks passed")


if __name__ == "__main__":
    main()
