"""Tests for `tools/ask_user_question.py` — voice-side structured-
question tool.

Validates the formatter + shape gates: 2-4 options, question-mark
requirement, header length cap, JSON-array input, multi-select
phrasing. The matching step (on the next user turn) is the
supervisor's prompt-side concern and isn't covered here.
"""
from __future__ import annotations

import asyncio
import json

import pytest


def _unwrap(tool):
    for attr in ("__livekit_agents_func", "_func", "fnc", "func", "callable"):
        f = getattr(tool, attr, None)
        if callable(f):
            return f
    if callable(tool):
        return tool
    raise RuntimeError(f"can't unwrap {tool!r}")


def _run(tool, **kwargs):
    return asyncio.run(_unwrap(tool)(**kwargs))


@pytest.fixture
def ask():
    from tools.ask_user_question import ask_user_question
    return ask_user_question


# ── happy paths ─────────────────────────────────────────────────


def test_two_options_renders_voice_friendly(ask):
    out = _run(ask,
        question="Which auth?",
        options_json=json.dumps(["JWT", "Sessions"]),
    )
    assert "Which auth?" in out
    assert "option one, JWT" in out
    assert "option two, Sessions" in out
    assert "Pick one" in out


def test_three_options(ask):
    out = _run(ask,
        question="Which database?",
        options_json=json.dumps(["Postgres", "SQLite", "DuckDB"]),
    )
    for tok in ("option one, Postgres", "option two, SQLite", "option three, DuckDB"):
        assert tok in out


def test_four_options(ask):
    """Boundary: 4 is the max allowed (voice working-memory cap)."""
    out = _run(ask,
        question="Pick one?",
        options_json=json.dumps(["A", "B", "C", "D"]),
    )
    assert "option four, D" in out


def test_multi_select_phrasing(ask):
    out = _run(ask,
        question="Which integrations to enable?",
        options_json=json.dumps(["Slack", "Discord", "Teams"]),
        multi_select=True,
    )
    assert "Pick one or more" in out
    # The number-word rendering still applies
    assert "option one, Slack" in out


def test_options_get_trimmed_of_whitespace(ask):
    out = _run(ask,
        question="Which?",
        options_json=json.dumps(["  Foo  ", "Bar"]),
    )
    assert "option one, Foo" in out
    assert "  Foo  " not in out


# ── validation: question shape ─────────────────────────────────


def test_empty_question_rejected(ask):
    out = _run(ask, question="   ", options_json=json.dumps(["A", "B"]))
    assert "non-empty" in out


def test_missing_question_mark_rejected(ask):
    out = _run(ask, question="Which auth", options_json=json.dumps(["A", "B"]))
    assert "must end with '?'" in out


# ── validation: options shape ──────────────────────────────────


def test_bad_options_json_rejected(ask):
    out = _run(ask, question="Q?", options_json="not json")
    assert "Bad options_json" in out


def test_options_must_be_array(ask):
    out = _run(ask, question="Q?", options_json=json.dumps({"oops": "dict"}))
    assert "must be a JSON array" in out


def test_options_must_be_strings(ask):
    out = _run(ask, question="Q?", options_json=json.dumps(["A", 42]))
    assert "is not a string" in out


def test_empty_option_string_rejected(ask):
    out = _run(ask, question="Q?", options_json=json.dumps(["A", "  "]))
    assert "empty" in out.lower()


def test_one_option_rejected(ask):
    """Single-option asks are pointless — should be a statement
    not a question, OR a yes/no in prose."""
    out = _run(ask, question="Q?", options_json=json.dumps(["Only choice"]))
    assert "at least 2 options" in out or "at least" in out.lower()


def test_five_options_rejected(ask):
    """Voice working memory cap at 4."""
    out = _run(ask, question="Q?", options_json=json.dumps(["A", "B", "C", "D", "E"]))
    assert "Cap at 4" in out or "more than" in out.lower()


# ── validation: header ────────────────────────────────────────


def test_header_length_cap(ask):
    """≤12 chars per claude-code's shape (parallels the tray UI)."""
    out = _run(ask,
        question="Q?", options_json=json.dumps(["A", "B"]),
        header="this header is way too long",
    )
    assert "Header too long" in out


def test_header_within_cap_accepted(ask):
    out = _run(ask,
        question="Q?", options_json=json.dumps(["A", "B"]),
        header="Auth method",  # 11 chars
    )
    assert "Q?" in out
    assert "option one, A" in out
