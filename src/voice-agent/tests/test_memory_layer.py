"""Tests for the voice agent's memory tools (jarvis_memory.py)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import jarvis_memory as jm


def _run(coro):
    """Use a fresh event loop each call so tests are isolated."""
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Pure helpers ──────────────────────────────────────────────────────


def test_memory_id_is_deterministic_sha256():
    a = jm._memory_id("User runs Pretva.")
    b = jm._memory_id("user runs pretva.")  # different case
    c = jm._memory_id("User runs Pretva.")  # exact match
    assert len(a) == 64  # sha256 hex
    assert a == c
    assert a == b  # normalize before hashing


def test_memory_id_changes_with_content():
    a = jm._memory_id("first thing")
    b = jm._memory_id("second thing")
    assert a != b


def test_normalize_strips_and_lowercases():
    assert jm._normalize("  HELLO World  ") == "hello world"


def test_is_sensitive_detects_common_credential_shapes():
    cases = [
        "OPENAI_API_KEY=sk-abc123",
        "my password is hunter2",
        "Bearer eyJhbGc",
        "ghp_xxxxxxxxxxxxxxxxxxxx",
        "token: ghp_xyz",
        "aws_secret_key = ABCDEF",
    ]
    for text in cases:
        assert jm._is_sensitive(text), f"missed: {text!r}"


def test_is_sensitive_passes_normal_facts():
    cases = [
        "User runs Pretva, a ride-hailing service in Cameroon.",
        "Prefers concise replies.",
        "Lives in Cameroon.",
        "Works on JARVIS, an AI assistant.",
    ]
    for text in cases:
        assert not jm._is_sensitive(text), f"false positive: {text!r}"


# ── remember() tool ──────────────────────────────────────────────────


def test_remember_publishes_upsert_event(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    result = _run(jm.remember._func(
        content="User runs Pretva, a ride-hailing service in Cameroon.",
        category="identity",
    ))
    assert "saved" in result.lower()
    assert len(captured) == 1
    et, payload = captured[0]
    assert et == "memory.value.upserted"
    assert payload["category"] == "identity"
    assert "Pretva" in payload["content"]
    assert payload["memory_id"] == jm._memory_id(payload["content"])


def test_remember_blocks_sensitive_content(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    cases = [
        "OPENAI_API_KEY=sk-abc123",
        "my password is hunter2",
        "ghp_aaaaaaaaaaaaaaaaaaaa",
    ]
    for text in cases:
        result = _run(jm.remember._func(content=text))
        assert (
            "credential" in result.lower()
            or "won't store" in result.lower()
        ), f"not blocked: {text!r} → {result!r}"
    assert captured == [], "sensitive content was published"


def test_remember_rejects_overlong(monkeypatch):
    monkeypatch.setattr(jm, "_publish_event", lambda *a, **kw: None)
    long = "x" * 600
    result = _run(jm.remember._func(content=long))
    assert "too long" in result.lower() or "500" in result


def test_remember_empty_input(monkeypatch):
    monkeypatch.setattr(jm, "_publish_event", lambda *a, **kw: None)
    result = _run(jm.remember._func(content=""))
    assert "empty" in result.lower() or "nothing" in result.lower()


def test_remember_normalizes_invalid_category(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    _run(jm.remember._func(content="Some fact.", category="nonsense"))
    assert captured[0][1]["category"] == "fact"


# ── forget() tool ────────────────────────────────────────────────────


def test_forget_publishes_remove_event(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [{"memory_id": "abc", "content": "User runs Pretva"}],
    )
    result = _run(jm.forget._func(query="Pretva"))
    assert "forgotten" in result.lower()
    assert captured[0][0] == "memory.value.removed"
    assert captured[0][1]["memory_id"] == "abc"


def test_forget_no_match_returns_friendly_message(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [{"memory_id": "x", "content": "totally unrelated"}],
    )
    result = _run(jm.forget._func(query="nonexistent"))
    assert "no match" in result.lower()
    assert captured == []


def test_forget_empty_query(monkeypatch):
    monkeypatch.setattr(jm, "_publish_event", lambda *a, **kw: None)
    result = _run(jm.forget._func(query=""))
    assert "what should i forget" in result.lower() or "no query" in result.lower()


# ── list_memories() tool ─────────────────────────────────────────────


def test_list_memories_voice_format(monkeypatch):
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [
            {"content": "Lives in Cameroon", "category": "identity",
             "use_count": 3},
            {"content": "Prefers terse replies", "category": "preference",
             "use_count": 1},
        ],
    )
    result = _run(jm.list_memories._func())
    assert "Lives in Cameroon" in result
    assert "Prefers terse replies" in result
    assert "[identity]" in result
    assert "[preference]" in result


def test_list_memories_empty(monkeypatch):
    monkeypatch.setattr(jm, "_read_memories_via_sdk", lambda **kw: [])
    result = _run(jm.list_memories._func())
    assert "haven't saved" in result.lower() or "no memories" in result.lower()


# ── format_memories_for_prompt — system-prompt injection ─────────────


def test_format_memories_for_prompt_with_rows(monkeypatch):
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "m1", "content": "User runs Pretva.",
             "category": "project"},
            {"memory_id": "m2", "content": "Prefers terse replies.",
             "category": "preference"},
        ],
    )
    bumped = []
    monkeypatch.setattr(
        jm, "_bump_uses_via_sdk", lambda ids: bumped.extend(ids),
    )
    block = jm.format_memories_for_prompt(top_n=10)
    assert "## What you remember about Ulrich" in block
    assert "User runs Pretva." in block
    assert "[project]" in block
    assert bumped == ["m1", "m2"]


def test_format_memories_empty_returns_blank(monkeypatch):
    monkeypatch.setattr(jm, "_read_memories_via_sdk", lambda **kw: [])
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)
    assert jm.format_memories_for_prompt(top_n=10) == ""


def test_format_memories_top_n_from_env(monkeypatch):
    """JARVIS_MEMORY_TOP_N controls the cap when top_n is None."""
    seen_limit = []
    monkeypatch.setenv("JARVIS_MEMORY_TOP_N", "7")
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: (seen_limit.append(kw.get("limit")) or []),
    )
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)
    jm.format_memories_for_prompt()
    assert seen_limit == [7]
