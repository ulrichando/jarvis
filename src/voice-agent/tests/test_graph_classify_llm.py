"""LLM classifier produces a strict JSON {route, confidence} object
for utterances the regex doesn't catch. Mock the LLM so the test
doesn't hit Groq."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_classify_with_llm_extracts_route_and_confidence():
    from supervisor_graph.classify import classify_with_llm

    # Mock the inner Groq client to return a strict-JSON content.
    fake_response = MagicMock()
    fake_response.content = '{"route": "REASONING", "confidence": 0.92}'

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(return_value=fake_response)

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_chain,
    ):
        result = classify_with_llm("explain how recursion works", history=[])

    assert result == {"route": "REASONING", "confidence": 0.92}


def test_classify_with_llm_falls_back_to_banter_on_parse_error():
    from supervisor_graph.classify import classify_with_llm

    fake_response = MagicMock()
    fake_response.content = "garbage, not json"

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(return_value=fake_response)

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_chain,
    ):
        result = classify_with_llm("hello?", history=[])

    # Conservative default: BANTER with low confidence.
    assert result["route"] == "BANTER"
    assert result["confidence"] <= 0.3
