"""Confab detector × auto-extractor coordination (fix N in the
2026-05-08 audit).

Live capture 2026-05-08T13:18: two consecutive "saved" claims dropped
by confab detector because the supervisor never calls remember() —
the v2 auto-extractor architecture owns memory writes off-band, so
the supervisor's chat_ctx contains no tool evidence for save replies.

Fix: when a successful extraction landed within the last 30 s, the
confab detector treats it as tool-equivalent evidence.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("JARVIS_CONFAB_DETECTOR", "1")


def test_save_claim_dropped_when_no_extractor_evidence():
    """Baseline: a 'saved' claim with no extractor success and no
    tool evidence in chat_ctx must still be flagged as confab."""
    import pipeline.memory_extractor as me
    from confab_detector import looks_like_confabulation
    me._LAST_EXTRACTION_SUCCESS_AT = None
    is_confab, reason = looks_like_confabulation(
        "It's saved as permanent memory, sir.",
        prior_messages=[],
    )
    assert is_confab, f"baseline expected confab, got reason={reason!r}"


def test_save_claim_passes_with_recent_extractor_success():
    """When the extractor wrote a fact within the last 30 s, the
    'saved' reply must pass the gate."""
    import pipeline.memory_extractor as me
    from confab_detector import looks_like_confabulation
    me._LAST_EXTRACTION_SUCCESS_AT = time.time()
    is_confab, reason = looks_like_confabulation(
        "It's saved as permanent memory, sir.",
        prior_messages=[],
    )
    assert not is_confab, (
        f"recent extractor success should grant evidence; reason={reason!r}"
    )


def test_save_claim_dropped_when_extractor_success_is_stale():
    """A 5-minute-old extractor success must NOT grant evidence to
    an unrelated confab on the current turn."""
    import pipeline.memory_extractor as me
    from confab_detector import looks_like_confabulation
    me._LAST_EXTRACTION_SUCCESS_AT = time.time() - 300  # 5 min ago
    is_confab, reason = looks_like_confabulation(
        "I've saved the note about your wife.",
        prior_messages=[],
    )
    assert is_confab, (
        f"stale extractor success should NOT grant evidence; reason={reason!r}"
    )


def test_extractor_marks_success_on_parse():
    """`extract_memory_from_turn` must call _mark_extraction_success
    when the LLM returns a parseable result."""
    import asyncio
    import pipeline.memory_extractor as me

    me._LAST_EXTRACTION_SUCCESS_AT = None

    async def fake_llm(_transcript):
        return "user: Ulrich's wife is named Lizzy."

    me._call_extractor_llm = fake_llm  # monkeypatch
    asyncio.new_event_loop().run_until_complete(
        me.extract_memory_from_turn("my wife's name is Lizzy")
    )
    assert me._LAST_EXTRACTION_SUCCESS_AT is not None, (
        "extractor success not marked"
    )
    assert me.has_recent_extraction_evidence(), (
        "has_recent_extraction_evidence not True after fresh success"
    )


def test_skip_does_not_mark_success():
    """SKIP-only output must not mark a success — that would grant
    evidence to a confab on the next turn just because the user
    said something un-extractable like 'yeah okay'."""
    import asyncio
    import pipeline.memory_extractor as me

    me._LAST_EXTRACTION_SUCCESS_AT = None

    async def fake_skip(_transcript):
        return "SKIP"

    me._call_extractor_llm = fake_skip
    asyncio.new_event_loop().run_until_complete(
        me.extract_memory_from_turn("yeah okay")
    )
    assert me._LAST_EXTRACTION_SUCCESS_AT is None
    assert not me.has_recent_extraction_evidence()


# ── Save-claim gate on extraction-evidence path ──────────────────────


@pytest.mark.parametrize("save_claim_text", [
    "It's saved as permanent memory, sir.",
    "Noted, sir.",
    "Got it. Saved that.",
    "I'll remember that.",
    "Remembered.",
    "Added that to memory.",
    "Made a note of it.",
])
def test_save_claims_pass_with_extractor_evidence(save_claim_text):
    """All flavors of save-claim must pass the gate when the
    extractor wrote within the last 30 s."""
    import pipeline.memory_extractor as me
    from confab_detector import looks_like_confabulation
    me._LAST_EXTRACTION_SUCCESS_AT = time.time()
    is_confab, reason = looks_like_confabulation(save_claim_text, prior_messages=[])
    assert not is_confab, (
        f"save claim {save_claim_text!r} should pass with fresh extractor "
        f"evidence; reason={reason!r}"
    )


@pytest.mark.parametrize("non_save_confab_text", [
    "Done, sir, opened a tab.",
    "I've opened that for you.",
    "I've launched chrome.",
    "Screenshot taken.",
    "Posted that to your feed.",
    "A new tab is open, sir.",
])
def test_non_save_confab_still_flagged_with_fresh_extractor(non_save_confab_text):
    """The extraction-evidence path is gated by save-claim shape:
    an unrelated confab ('Browser opened, sir.') landing inside the
    30 s window MUST still be flagged. Without this gate, every
    confab in the post-extraction window slips through.

    Live failure shape: extractor fires for 'Lizzie is my wife' →
    25 s later supervisor confabulates 'Browser opened, sir.' →
    pre-fix this passed because extraction-evidence was global, not
    save-claim-gated."""
    import pipeline.memory_extractor as me
    from confab_detector import looks_like_confabulation
    me._LAST_EXTRACTION_SUCCESS_AT = time.time()
    is_confab, reason = looks_like_confabulation(non_save_confab_text, prior_messages=[])
    assert is_confab, (
        f"non-save confab {non_save_confab_text!r} should still be flagged "
        f"despite fresh extractor evidence; reason={reason!r}"
    )
