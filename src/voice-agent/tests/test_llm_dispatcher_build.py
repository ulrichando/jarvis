"""Verify `build_dispatching_llm` per-route defaults and overrides.

Refactor 2026-05-23: Anthropic Haiku/Sonnet become rung-1 primaries
(prompt-cached → ~700 ms TTFW warm) with Groq legacy demoted to rung 2
and DeepSeek-v4-flash as rung 3.

These tests pin the contract:

* default per-route primary IS Anthropic when ANTHROPIC_API_KEY is set;
* `JARVIS_{BANTER,TASK,REASONING,EMOTIONAL}_MODEL` env knobs swap the
  Anthropic model id per route at build time;
* `task_override` parameter still wins over the TASK env (tray pin);
* without ANTHROPIC_API_KEY the dispatcher still boots, falling back
  to the Groq legacy primaries (graceful degrade);
* the FallbackAdapter rungs include Groq legacy + DeepSeek-v4-flash so
  a single Anthropic outage doesn't strand the route.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Tests run from the voice-agent root.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Groq plugin's constructor reads GROQ_API_KEY at __init__ time even
# when the request never goes out — same pattern as test_breaker_shims.
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
# DeepSeek + Anthropic likewise — make sure the rungs construct.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _labels(dispatch) -> dict[str, str]:
    """Return {route: _jarvis_label} for the four routes."""
    out = {}
    for route in ("BANTER", "TASK", "REASONING", "EMOTIONAL"):
        inner = dispatch.pick(route)
        out[route] = getattr(inner, "_jarvis_label", repr(inner))
    return out


def _wipe_route_env(monkeypatch) -> None:
    """Strip any per-route override env vars left over from other tests."""
    for var in (
        "JARVIS_BANTER_MODEL",
        "JARVIS_TASK_MODEL",
        "JARVIS_REASONING_MODEL",
        "JARVIS_EMOTIONAL_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_default_primaries_are_anthropic(monkeypatch):
    """With ANTHROPIC_API_KEY set and no env overrides, the four routes
    must land on the spec'd Anthropic primaries: Haiku 4.5 for BANTER /
    TASK / EMOTIONAL and Sonnet 4.6 for REASONING."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    from providers.llm import build_dispatching_llm

    labels = _labels(build_dispatching_llm())
    assert labels["BANTER"] == "anthropic:claude-haiku-4-5"
    assert labels["TASK"] == "anthropic:claude-haiku-4-5"
    assert labels["REASONING"] == "anthropic:claude-sonnet-4-6"
    assert labels["EMOTIONAL"] == "anthropic:claude-haiku-4-5"


def test_env_overrides_swap_primary_model(monkeypatch):
    """Each per-route env var must replace the default Anthropic model
    id while keeping the provider tier (anthropic:) intact."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("JARVIS_BANTER_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("JARVIS_TASK_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("JARVIS_REASONING_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("JARVIS_EMOTIONAL_MODEL", "claude-sonnet-4-6")

    from providers.llm import build_dispatching_llm

    labels = _labels(build_dispatching_llm())
    assert labels["BANTER"] == "anthropic:claude-sonnet-4-6"
    assert labels["TASK"] == "anthropic:claude-opus-4-7"
    assert labels["REASONING"] == "anthropic:claude-opus-4-7"
    assert labels["EMOTIONAL"] == "anthropic:claude-sonnet-4-6"


def test_task_override_wins_over_env(monkeypatch):
    """When the caller passes `task_override`, that LLM lands on TASK
    regardless of JARVIS_TASK_MODEL. Other routes stay on the env-
    derived (or default) Anthropic primaries."""
    from unittest.mock import MagicMock

    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("JARVIS_TASK_MODEL", "claude-opus-4-7")

    pinned = MagicMock(spec=["_jarvis_label"])
    pinned._jarvis_label = "tray-pinned:gpt-5-mini"

    from providers.llm import build_dispatching_llm

    d = build_dispatching_llm(task_override=pinned)
    labels = _labels(d)
    # task_override beats the env for TASK.
    assert labels["TASK"] == "tray-pinned:gpt-5-mini"
    # Other routes keep their Anthropic defaults.
    assert labels["BANTER"] == "anthropic:claude-haiku-4-5"
    assert labels["REASONING"] == "anthropic:claude-sonnet-4-6"
    assert labels["EMOTIONAL"] == "anthropic:claude-haiku-4-5"


def test_no_anthropic_key_falls_back_to_groq_primaries(monkeypatch):
    """When ANTHROPIC_API_KEY is unset, every route must still build —
    the route's Groq legacy primary takes the rung-1 slot. Dispatcher
    refuses to refuse-to-boot (no Anthropic key is a normal CI state)."""
    _wipe_route_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from providers.llm import build_dispatching_llm

    labels = _labels(build_dispatching_llm())
    assert labels["BANTER"] == "groq:qwen/qwen3.6-27b"
    assert labels["TASK"] == "groq:openai/gpt-oss-120b"
    assert labels["REASONING"] == "groq:qwen/qwen3-32b"
    assert labels["EMOTIONAL"] == "groq:qwen/qwen3.6-27b"


def test_no_anthropic_key_with_task_override_still_wins(monkeypatch):
    """The graceful-degrade path must STILL honor `task_override`."""
    from unittest.mock import MagicMock

    _wipe_route_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    pinned = MagicMock(spec=["_jarvis_label"])
    pinned._jarvis_label = "tray-pinned:gpt-5-mini"

    from providers.llm import build_dispatching_llm

    labels = _labels(build_dispatching_llm(task_override=pinned))
    assert labels["TASK"] == "tray-pinned:gpt-5-mini"
    assert labels["BANTER"] == "groq:qwen/qwen3.6-27b"


def test_fallback_chain_includes_groq_and_deepseek(monkeypatch):
    """A route built with Anthropic primary AND both DEEPSEEK_API_KEY +
    GROQ_API_KEY set must be wrapped in a FallbackAdapter with at least
    three rungs: [anthropic-primary, groq-legacy, deepseek-v4-flash].
    Single-provider outage on any one rung cascades to the next."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")

    from livekit.agents.llm import FallbackAdapter

    from providers.llm import build_dispatching_llm

    d = build_dispatching_llm()
    for route in ("BANTER", "TASK", "REASONING", "EMOTIONAL"):
        inner = d.pick(route)
        # Each route inner must be a FallbackAdapter (not a bare LLM).
        assert isinstance(inner, FallbackAdapter), (
            f"route {route} expected FallbackAdapter, got {type(inner).__name__}"
        )
        # Walk the adapter's internal rung list. livekit-agents exposes
        # the LLM rungs via a private attribute, but the shape is
        # stable in the pinned version. We look for both labels by
        # asking each candidate rung for its _jarvis_label.
        rungs = (
            getattr(inner, "_llm_instances", None)
            or getattr(inner, "_llms", None)
            or []
        )
        labels = [getattr(r, "_jarvis_label", "") for r in rungs]
        # Rung 1 must be Anthropic.
        assert labels and labels[0].startswith("anthropic:"), (
            f"route {route} rung 1 expected anthropic, got {labels[0] if labels else 'none'}"
        )
        # Groq legacy and DeepSeek must be present somewhere in the chain.
        assert any(lbl.startswith("groq:") for lbl in labels), (
            f"route {route} missing Groq rung; labels={labels}"
        )
        assert any(lbl.startswith("deepseek:") for lbl in labels), (
            f"route {route} missing DeepSeek rung; labels={labels}"
        )


def test_dispatcher_fallback_field_matches_task(monkeypatch):
    """`DispatchingLLM.fallback` must be the TASK inner (post-override).
    Used by the framework when the route tag is unrecognized — losing
    the link to TASK would change LLM selection for unknown routes."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    from providers.llm import build_dispatching_llm

    d = build_dispatching_llm()
    assert d.fallback is d.inners["TASK"]


def test_empty_env_override_falls_back_to_default(monkeypatch):
    """An empty-string env override must NOT pin an empty model name —
    we treat empty as 'use default' so a half-deleted env var doesn't
    crash the dispatcher build."""
    _wipe_route_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("JARVIS_TASK_MODEL", "")  # empty → ignore
    monkeypatch.setenv("JARVIS_BANTER_MODEL", "   ")  # whitespace → ignore

    from providers.llm import build_dispatching_llm

    labels = _labels(build_dispatching_llm())
    assert labels["TASK"] == "anthropic:claude-haiku-4-5"
    assert labels["BANTER"] == "anthropic:claude-haiku-4-5"
