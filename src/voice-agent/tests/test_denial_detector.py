# src/voice-agent/tests/test_denial_detector.py
"""Tests for the output-rail denial detector.

Watches the supervisor LLM's outgoing assistant text. If the text
matches the denial pattern AND no memory write/read fired this turn,
the detector suppresses the reply and signals a re-roll with forced
tool_choice.

JARVIS-original — no published precedent for capability-denial
specifically. Closest analog is LLM-Guard's NoRefusal scanner.
"""
from __future__ import annotations
import pytest
from sanitizers.denial_detector import is_capability_denial


@pytest.mark.parametrize("text", [
    "I'm a conversational AI, I don't retain information between conversations.",
    "I'm just an AI assistant, I can't remember between sessions.",
    "I'm afraid I don't have the ability to store or recall individual names or memories.",
    "I'm a language model, I don't retain information about individual users.",
    "I won't be able to recall it later — I don't have memory.",
    "Each time you interact with me, it's a new conversation, I don't store anything.",
])
def test_matches_capability_denials(text):
    assert is_capability_denial(text) is True


@pytest.mark.parametrize("text", [
    "Of course, sir.",
    "I can't open a tab — that's a desktop task.",            # tool refusal, not memory
    "I can't generate physical money.",                        # legitimate inability
    "Lizzy, sir.",                                             # successful recall reply
    "I don't have that yet, sir — want me to remember it now?", # honest empty
    "I'm not able to find what you mentioned.",                # vague but not a denial
    "I haven't been told that yet.",                           # honest empty (different shape)
])
def test_does_not_match_non_denials(text):
    assert is_capability_denial(text) is False


def test_install_is_idempotent():
    """install() must be safe to call multiple times (matches the
    existing sanitizer convention)."""
    import sanitizers.denial_detector as dd
    dd.install()
    dd.install()  # should not raise / should not double-patch
