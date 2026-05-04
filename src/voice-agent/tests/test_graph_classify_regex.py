"""Verb-initial regex must catch ~80% of TASK utterances at zero
latency. Source for the verb list: production traffic 2026-04 to
2026-05 — every observed user TASK started with one of these."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.mark.parametrize("utterance", [
    "open a new tab on the current browser",
    "Open Chrome.",
    "open YouTube",
    "play the news",
    "find me an iPhone on Amazon",
    "launch a terminal",
    "close that tab",
    "switch to firefox",
    "search google for the weather",
    "navigate to github.com",
    "read the page",
    "send a message",
    "create a new file",
    "delete that line",
    "post on twitter",
    "buy an iphone 15",
    # Imperative without explicit verb-leading punct
    "Open the docs, please",
    # Short imperatives still match
    "Open YouTube",
])
def test_verb_initial_regex_classifies_task(utterance):
    from supervisor_graph.classify import is_verb_initial_task
    assert is_verb_initial_task(utterance), (
        f"expected TASK match for {utterance!r}"
    )


@pytest.mark.parametrize("utterance", [
    "how are you",
    "what's up",
    "hey jarvis",
    "good morning",
    "thanks",
    "can you tell me a joke",
    "what time is it",   # question, not a command
    "do you remember last night",
    "I think we should refactor",
    "actually never mind",
])
def test_verb_initial_regex_rejects_non_task(utterance):
    from supervisor_graph.classify import is_verb_initial_task
    assert not is_verb_initial_task(utterance), (
        f"unexpected TASK match for {utterance!r}"
    )
