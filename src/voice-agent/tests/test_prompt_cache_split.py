"""Tests for the stable/volatile system-prompt split helpers.

Covers ``providers.prompt_cache``:
  - ``CACHE_BREAK_MARKER`` is the agreed-upon sentinel string.
  - ``assemble_with_marker`` produces a deterministic joined string.
  - ``split_system_text`` recovers the (stable, volatile) pair via
    either exact-prefix match (preferred) or marker fallback.
  - ``apply_stable_prefix_recursively`` walks LLM trees and calls
    ``set_stable_prefix`` on every wrapper that supports it.

And the assembly contract in ``_build_initial_prompt_state``:
  - The new keys ``stable_prefix`` and ``volatile_suffix`` are present.
  - ``initial_instructions`` is exactly
    ``assemble_with_marker(stable_prefix, volatile_suffix)``.
  - ``stable_prefix`` carries SOUL + JARVIS_INSTRUCTIONS +
    skill_catalog_block.
  - ``volatile_suffix`` carries runtime_id + memory + breaker.
  - The runtime_id_block is also exposed alongside the consolidated
    keys so the turn_dispatcher hot-reload path can rebuild the
    volatile half without re-parsing it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Tests run from the voice-agent root.
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")


# ──────────────────────────────────────────────────────────────────────
# providers.prompt_cache primitives
# ──────────────────────────────────────────────────────────────────────


def test_marker_is_recognisable():
    """The marker must be a non-empty, recognisable string that no
    handwritten prompt content would produce."""
    from providers.prompt_cache import CACHE_BREAK_MARKER

    assert isinstance(CACHE_BREAK_MARKER, str)
    assert CACHE_BREAK_MARKER
    # Triple-angle brackets + ALL_CAPS_WITH_UNDERSCORE is the agreed shape.
    assert CACHE_BREAK_MARKER.startswith("<<<")
    assert CACHE_BREAK_MARKER.endswith(">>>")
    assert "JARVIS_CACHE_BREAK" in CACHE_BREAK_MARKER


def test_assemble_with_marker_joins_with_marker():
    """The assembled output must contain the marker between stable and
    volatile, exactly once. Round-trips cleanly with split_system_text."""
    from providers.prompt_cache import (
        CACHE_BREAK_MARKER,
        assemble_with_marker,
        split_system_text,
    )

    stable = "STABLE PART"
    volatile = "VOLATILE PART"
    joined = assemble_with_marker(stable, volatile)
    assert CACHE_BREAK_MARKER in joined
    # Exactly one marker.
    assert joined.count(CACHE_BREAK_MARKER) == 1
    # Round-trip via marker fallback (no stable_prefix arg).
    recovered_stable, recovered_volatile = split_system_text(joined)
    assert recovered_stable == stable
    assert recovered_volatile == volatile


def test_assemble_with_marker_handles_empty_halves():
    """Empty stable OR empty volatile collapses cleanly — no marker,
    no leading/trailing whitespace junk."""
    from providers.prompt_cache import (
        CACHE_BREAK_MARKER,
        assemble_with_marker,
    )

    # Both empty → empty string.
    assert assemble_with_marker("", "") == ""
    # Only stable → just stable, no marker.
    assert assemble_with_marker("ONLY STABLE", "") == "ONLY STABLE"
    assert CACHE_BREAK_MARKER not in assemble_with_marker("ONLY STABLE", "")
    # Only volatile → just volatile, no marker.
    assert assemble_with_marker("", "ONLY VOLATILE") == "ONLY VOLATILE"


def test_split_with_exact_prefix_match():
    """When the caller knows the stable prefix, exact-prefix match is
    the cheapest path — no marker required, no scanning of arbitrary
    text. Returns the prefix verbatim + the remainder (trailing
    whitespace stripped, leading newline-only whitespace stripped so a
    marker-added newline doesn't pollute the volatile)."""
    from providers.prompt_cache import split_system_text

    stable = "EXACT STABLE PREFIX " * 40
    volatile = "VOLATILE REMAINDER"  # no surrounding whitespace
    full = stable + volatile  # NO marker

    s, v = split_system_text(full, stable_prefix=stable)
    assert s == stable
    assert v == volatile


def test_split_with_exact_prefix_and_marker_strips_marker():
    """When both an exact prefix AND the marker are present, the marker
    is stripped from the recovered volatile (the marker is plumbing,
    not content the LLM should see)."""
    from providers.prompt_cache import (
        CACHE_BREAK_MARKER,
        assemble_with_marker,
        split_system_text,
    )

    stable = "STABLE WITH MARKER"
    volatile = "VOLATILE"
    full = assemble_with_marker(stable, volatile)
    assert CACHE_BREAK_MARKER in full

    s, v = split_system_text(full, stable_prefix=stable)
    assert s == stable
    # Marker stripped — only the original volatile text remains.
    assert v == volatile
    assert CACHE_BREAK_MARKER not in v


def test_split_falls_back_to_no_split_when_unrecoverable():
    """When neither an exact prefix match nor a marker is present, the
    helper returns ``(full_text, "")`` so the caller falls through to
    no-cache rather than shipping a malformed split."""
    from providers.prompt_cache import split_system_text

    full = "FLAT PROMPT NO MARKER NO PREFIX"
    s, v = split_system_text(full, stable_prefix=None)
    assert s == full
    assert v == ""


def test_split_with_non_matching_prefix_falls_back_to_marker():
    """When the supplied stable_prefix doesn't match the system text,
    the helper falls back to marker-split (still recoverable)."""
    from providers.prompt_cache import assemble_with_marker, split_system_text

    real_stable = "REAL STABLE"
    real_volatile = "REAL VOLATILE"
    full = assemble_with_marker(real_stable, real_volatile)

    # Caller has the WRONG stable_prefix cached (e.g. drift).
    s, v = split_system_text(full, stable_prefix="WRONG PREFIX")
    # Marker fallback recovers the right split.
    assert s == real_stable
    assert v == real_volatile


# ──────────────────────────────────────────────────────────────────────
# apply_stable_prefix_recursively
# ──────────────────────────────────────────────────────────────────────


def test_apply_walks_dispatching_llm():
    """A DispatchingLLM whose `inners` dict contains LLMs with
    ``set_stable_prefix`` must have each one updated."""
    from providers.prompt_cache import apply_stable_prefix_recursively

    class _Wrapper:
        def __init__(self):
            self.received = None

        def set_stable_prefix(self, s):
            self.received = s

    w_banter = _Wrapper()
    w_task = _Wrapper()
    w_reasoning = _Wrapper()
    w_emotional = _Wrapper()

    # Stub DispatchingLLM-shaped object.
    class _Dispatcher:
        inners = {
            "BANTER": w_banter,
            "TASK": w_task,
            "REASONING": w_reasoning,
            "EMOTIONAL": w_emotional,
        }
        fallback = w_task

    stable = "STABLE"
    n = apply_stable_prefix_recursively(_Dispatcher(), stable)
    # All 4 unique wrappers updated (fallback is a duplicate of TASK).
    assert n == 4
    for w in (w_banter, w_task, w_reasoning, w_emotional):
        assert w.received == stable


def test_apply_walks_fallback_adapter():
    """A FallbackAdapter-shaped node (with `_llm_instances`) must have
    every rung visited."""
    from providers.prompt_cache import apply_stable_prefix_recursively

    class _Wrapper:
        def __init__(self):
            self.received = None

        def set_stable_prefix(self, s):
            self.received = s

    w1 = _Wrapper()
    w2 = _Wrapper()
    w3 = _Wrapper()

    class _FallbackAdapter:
        _llm_instances = [w1, w2, w3]

    n = apply_stable_prefix_recursively(_FallbackAdapter(), "STABLE")
    assert n == 3


def test_apply_skips_llms_without_setter():
    """LLMs that don't expose ``set_stable_prefix`` (e.g. plain Groq /
    OpenAI / DeepSeek instances) are silently skipped — they rely on
    auto-prefix-cache instead of explicit cache_control."""
    from providers.prompt_cache import apply_stable_prefix_recursively

    class _DumbLLM:
        pass  # no set_stable_prefix

    n = apply_stable_prefix_recursively(_DumbLLM(), "STABLE")
    assert n == 0


def test_apply_handles_empty_prefix():
    """Empty stable_prefix is a no-op — the walk skips entirely."""
    from providers.prompt_cache import apply_stable_prefix_recursively

    received: list[str] = []

    class _Wrapper:
        def set_stable_prefix(self, s):
            received.append(s)

    n = apply_stable_prefix_recursively(_Wrapper(), "")
    assert n == 0
    assert received == []


# ──────────────────────────────────────────────────────────────────────
# _build_initial_prompt_state contract
# ──────────────────────────────────────────────────────────────────────


def test_build_initial_prompt_state_emits_new_keys(monkeypatch):
    """The dict returned by ``_build_initial_prompt_state`` must expose
    the new ``stable_prefix`` and ``volatile_suffix`` keys alongside
    the legacy keys."""
    import jarvis_agent as ja

    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda sid: "\n\n[runtime-id]")
    monkeypatch.setattr(ja, "_build_memory_block", lambda: "\n\n[memory]")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda: "\n\n[breaker]")

    state = ja._build_initial_prompt_state("test-speech")

    # New cache-aware keys exist.
    assert "stable_prefix" in state
    assert "volatile_suffix" in state
    assert "runtime_id_block" in state
    # Legacy keys preserved for backward compat.
    assert "instructions_prefix" in state
    assert "memory_block" in state
    assert "breaker_block" in state
    assert "skill_catalog_block" in state
    assert "initial_instructions" in state


def test_stable_prefix_carries_soul_and_instructions(monkeypatch):
    """``stable_prefix`` must contain SOUL + JARVIS_INSTRUCTIONS +
    skill_catalog_block — nothing volatile."""
    import jarvis_agent as ja

    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda sid: "\n\n[runtime-id-V0]")
    monkeypatch.setattr(ja, "_build_memory_block", lambda: "\n\n[memory-V0]")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda: "\n\n[breaker-V0]")

    state = ja._build_initial_prompt_state("test-speech")
    stable = state["stable_prefix"]

    # Stable contains identity + ops.
    assert ja.SOUL in stable
    assert ja.JARVIS_INSTRUCTIONS in stable

    # Stable does NOT contain anything volatile.
    assert "[runtime-id-V0]" not in stable
    assert "[memory-V0]" not in stable
    assert "[breaker-V0]" not in stable


def test_volatile_suffix_carries_runtime_memory_breaker(monkeypatch):
    """``volatile_suffix`` must be exactly
    ``runtime_id_block + memory_block + breaker_block`` so changes to
    any of the three rebuild it cleanly without re-parsing."""
    import jarvis_agent as ja

    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda sid: "RID")
    monkeypatch.setattr(ja, "_build_memory_block", lambda: "MEM")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda: "BRK")

    state = ja._build_initial_prompt_state("test-speech")
    assert state["volatile_suffix"] == "RIDMEMBRK"
    assert state["runtime_id_block"] == "RID"


def test_initial_instructions_uses_marker_assembly(monkeypatch):
    """``initial_instructions`` must equal
    ``assemble_with_marker(stable_prefix, volatile_suffix)`` so the
    LLM wrappers can recover the split via either exact-prefix match
    or the marker."""
    import jarvis_agent as ja

    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda sid: "RID")
    monkeypatch.setattr(ja, "_build_memory_block", lambda: "MEM")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda: "BRK")

    from providers.prompt_cache import assemble_with_marker

    state = ja._build_initial_prompt_state("test-speech")
    expected = assemble_with_marker(
        state["stable_prefix"], state["volatile_suffix"]
    )
    assert state["initial_instructions"] == expected


def test_initial_instructions_stable_comes_first(monkeypatch):
    """The whole point of the refactor is stable-first ordering for
    auto-prefix-cache providers (OpenAI / DeepSeek / Groq). The stable
    prefix must appear before any volatile content in the assembled
    string."""
    import jarvis_agent as ja

    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda sid: "RUNTIME_TAG")
    monkeypatch.setattr(ja, "_build_memory_block", lambda: "MEMORY_TAG")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda: "BREAKER_TAG")

    state = ja._build_initial_prompt_state("test-speech")
    fi = state["initial_instructions"]

    # SOUL appears before any of the volatile tags.
    soul_idx = fi.find("═══ WHO YOU ARE ═══")
    runtime_idx = fi.find("RUNTIME_TAG")
    memory_idx = fi.find("MEMORY_TAG")
    breaker_idx = fi.find("BREAKER_TAG")

    assert soul_idx >= 0
    assert runtime_idx >= 0
    assert memory_idx >= 0
    assert breaker_idx >= 0

    assert soul_idx < runtime_idx
    assert soul_idx < memory_idx
    assert soul_idx < breaker_idx
    # And the marker sits between the last stable content and the first
    # volatile content.
    from providers.prompt_cache import CACHE_BREAK_MARKER
    marker_idx = fi.find(CACHE_BREAK_MARKER)
    assert marker_idx > soul_idx
    assert marker_idx < runtime_idx
