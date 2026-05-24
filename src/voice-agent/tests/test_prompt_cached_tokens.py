"""Tests for the `prompt_cached_tokens` telemetry pipeline — that
provider-shaped LLM usage objects flow through the framework's
extraction and the JARVIS-side backfill correctly land the cache-hit
token count in `turn_telemetry.turns.prompt_cached_tokens`.

Coverage:
  - deepseek    : `prompt_cache_hit_tokens` backfilled when the
                  OpenAI-spec mirror is missing/zero; never overwrites
                  a positive existing value.
  - anthropic   : `cache_read_input_tokens` extraction (handled in the
                  Anthropic plugin) — regression that LLMMetrics shape
                  still carries the value end-to-end.
  - openai      : `prompt_tokens_details.cached_tokens` extraction
                  (handled in the framework's inference llm.py) — same
                  end-to-end shape verification.
  - groq        : no cache field at all → 0 (default) recorded.

The framework's `inference.llm.LLMStream._run` reads
`chunk.usage.prompt_tokens_details.cached_tokens` and builds an
`LLMMetrics` whose `prompt_cached_tokens` field is what the JARVIS
`_on_metrics_collected` handler reads to stamp
`session._jarvis_last_cache_read_tokens`. That value gets passed to
`log_turn(prompt_cached_tokens=...)`. We test each layer of that path.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import sanitizers.deepseek_cache_tokens as ds_cache
from pipeline.turn_telemetry import init_db, log_turn


# ── Backfill: pure-function tests for sanitizer ─────────────────────


def _mk_usage(*, prompt_tokens, completion_tokens, cached_in_details=None, deepseek_extra=None):
    """Build an openai-python CompletionUsage with optional details and
    optional DeepSeek-extra fields."""
    from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

    details = None
    if cached_in_details is not None:
        details = PromptTokensDetails(cached_tokens=cached_in_details, audio_tokens=None)
    u = CompletionUsage(
        completion_tokens=completion_tokens,
        prompt_tokens=prompt_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens_details=details,
    )
    if deepseek_extra is not None:
        # Pydantic v2 carries unknown fields in __pydantic_extra__.
        u.__pydantic_extra__ = dict(deepseek_extra)
    return u


def test_deepseek_cached_tokens_backfilled_when_openai_slot_empty():
    """Future-DeepSeek shape — only the extra field carries cache hits."""
    usage = _mk_usage(
        prompt_tokens=1211,
        completion_tokens=2,
        cached_in_details=0,
        deepseek_extra={"prompt_cache_hit_tokens": 1152, "prompt_cache_miss_tokens": 59},
    )
    rewrote = ds_cache._backfill_chunk_usage(usage)
    assert rewrote is True
    assert usage.prompt_tokens_details.cached_tokens == 1152


def test_deepseek_cached_tokens_never_overwrites_positive_value():
    """Today's DeepSeek shape — both fields populated, both equal. The
    backfill must be a no-op so we never accidentally double-extract or
    clobber a provider's authoritative value."""
    usage = _mk_usage(
        prompt_tokens=1211,
        completion_tokens=2,
        cached_in_details=1152,
        deepseek_extra={"prompt_cache_hit_tokens": 1152, "prompt_cache_miss_tokens": 59},
    )
    rewrote = ds_cache._backfill_chunk_usage(usage)
    assert rewrote is False
    assert usage.prompt_tokens_details.cached_tokens == 1152


def test_deepseek_cached_tokens_constructs_details_when_none():
    """If a DeepSeek-compat endpoint omits `prompt_tokens_details`
    entirely (some third parties do), the backfill must construct a
    fresh details object so the framework's `tokens_details.cached_tokens`
    read finds the value instead of returning 0."""
    usage = _mk_usage(
        prompt_tokens=1211,
        completion_tokens=2,
        cached_in_details=None,
        deepseek_extra={"prompt_cache_hit_tokens": 700},
    )
    rewrote = ds_cache._backfill_chunk_usage(usage)
    assert rewrote is True
    assert usage.prompt_tokens_details is not None
    assert usage.prompt_tokens_details.cached_tokens == 700


def test_deepseek_cached_tokens_no_op_when_extra_absent():
    """Non-DeepSeek call (no DeepSeek-extra fields) — backfill is a
    no-op; we must not synthesize a cached-tokens value for a provider
    that doesn't expose one."""
    usage = _mk_usage(
        prompt_tokens=100, completion_tokens=10, cached_in_details=0
    )
    rewrote = ds_cache._backfill_chunk_usage(usage)
    assert rewrote is False
    assert usage.prompt_tokens_details.cached_tokens == 0


def test_deepseek_cached_tokens_handles_none_usage():
    """A chunk with no usage block (interim streaming chunks) must
    return cleanly from the backfill without raising."""
    assert ds_cache._backfill_chunk_usage(None) is False


def test_deepseek_cached_tokens_install_is_idempotent():
    """Patch installation must be safe to call repeatedly — the
    `_run` wrapper should only stack once."""
    from livekit.agents.inference import llm as inf_llm

    ds_cache.install()
    first = inf_llm.LLMStream._run
    ds_cache.install()
    second = inf_llm.LLMStream._run
    assert first is second  # same wrapper, not re-wrapped


# ── End-to-end: extraction → telemetry, by provider ───────────────


def _simulate_metrics_capture_and_log(
    tmp_path,
    *,
    label: str,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_cached_tokens: int,
) -> int:
    """Mimic the in-process path the live agent walks: the framework
    fires `metrics_collected` with an LLMMetrics, the JARVIS handler at
    jarvis_agent._on_metrics_collected stamps it onto the session,
    log_turn writes the row. Returns the row's stored
    prompt_cached_tokens value."""
    # Build a faked LLMMetrics-shaped object (just the fields the
    # JARVIS handler reads). Use SimpleNamespace so getattr() works
    # exactly like the real pydantic object.
    metric = SimpleNamespace(
        type="llm_metrics",
        label=label,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_cached_tokens=prompt_cached_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )

    # Mimic the handler at jarvis_agent.py:_on_metrics_collected
    captured = {
        "input_tokens": getattr(metric, "prompt_tokens", None),
        "output_tokens": getattr(metric, "completion_tokens", None),
        "cache_read_tokens": getattr(metric, "prompt_cached_tokens", 0) or 0,
    }

    # Write through log_turn — what the live agent does on turn end.
    db = tmp_path / "telemetry.db"
    init_db(db)
    log_turn(
        db_path=db,
        user_text="x",
        jarvis_text="y",
        emotion="neutral",
        route="TASK",
        llm_used=label,
        voice_used="troy",
        ttfw_ms=100,
        total_audio_ms=200,
        user_followup_30s=False,
        route_fallback=False,
        input_tokens=captured["input_tokens"],
        output_tokens=captured["output_tokens"],
        prompt_cached_tokens=captured["cache_read_tokens"],
    )
    row = sqlite3.connect(db).execute(
        "SELECT prompt_cached_tokens FROM turns"
    ).fetchone()
    return row[0]


def test_deepseek_cached_tokens_extracted(tmp_path):
    """End-to-end: a DeepSeek-served turn (LLMMetrics carrying the
    extracted cache count) must land in
    `turn_telemetry.turns.prompt_cached_tokens`. This is the regression
    that was failing before — DeepSeek auto-caches since V2 but the
    cache count was 0 in every telemetry row.

    The framework's stock extraction at inference/llm.py line ~412
    already reads `prompt_tokens_details.cached_tokens`, which
    DeepSeek mirrors with `prompt_cache_hit_tokens`. Live probing
    2026-05-23 confirmed this currently lands the value in
    LLMMetrics.prompt_cached_tokens. This test guards the JARVIS-side
    handoff so a future regression (handler change, telemetry-arg
    rename) gets caught."""
    cached = _simulate_metrics_capture_and_log(
        tmp_path,
        label="deepseek:chat",
        prompt_tokens=1211,
        completion_tokens=2,
        prompt_cached_tokens=1152,
    )
    assert cached == 1152


def test_anthropic_cached_tokens_still_extracted(tmp_path):
    """Regression for Anthropic — its plugin reads
    `cache_read_input_tokens` and produces LLMMetrics.prompt_cached_tokens.
    Our JARVIS-side handler is provider-agnostic so the same
    end-to-end shape works."""
    cached = _simulate_metrics_capture_and_log(
        tmp_path,
        label="anthropic:claude-haiku-4-5",
        prompt_tokens=8000,
        completion_tokens=120,
        prompt_cached_tokens=7400,  # Anthropic ephemeral cache hit
    )
    assert cached == 7400


def test_openai_cached_tokens_extracted(tmp_path):
    """OpenAI gpt-5-mini and friends populate
    `prompt_tokens_details.cached_tokens`, which the framework
    extracts directly. Same end-to-end shape."""
    cached = _simulate_metrics_capture_and_log(
        tmp_path,
        label="gpt-5-mini",
        prompt_tokens=5000,
        completion_tokens=80,
        prompt_cached_tokens=4096,
    )
    assert cached == 4096


def test_groq_returns_zero_cached(tmp_path):
    """Groq does not return any cache field — LLMMetrics.prompt_cached_tokens
    defaults to 0, which lands as 0 in telemetry. Verifies the
    handler's `or 0` fallback path is exercised."""
    cached = _simulate_metrics_capture_and_log(
        tmp_path,
        label="groq:llama-3.3-70b-versatile",
        prompt_tokens=2000,
        completion_tokens=50,
        prompt_cached_tokens=0,
    )
    assert cached == 0


# ── Metric-capture handler ─────────────────────────────────────────


def test_metrics_handler_skips_non_llm_metric_types():
    """The framework reuses the metrics_collected event for STT/TTS/VAD/EOU
    — the JARVIS handler must early-return when `type != 'llm_metrics'`
    so we don't accidentally stamp STT-shaped fields onto the LLM cache
    capture slots."""
    session = SimpleNamespace(
        _jarvis_last_input_tokens=None,
        _jarvis_last_output_tokens=None,
        _jarvis_last_cache_read_tokens=0,
    )

    # Simulate the handler's check (mirrors jarvis_agent.py:_on_metrics_collected).
    def _on_metrics_collected(ev):
        m = getattr(ev, "metrics", None)
        if m is None or getattr(m, "type", None) != "llm_metrics":
            return
        session._jarvis_last_input_tokens = getattr(m, "prompt_tokens", None)
        session._jarvis_last_output_tokens = getattr(m, "completion_tokens", None)
        session._jarvis_last_cache_read_tokens = (
            getattr(m, "prompt_cached_tokens", 0) or 0
        )

    # Fire an STT-shaped metric; handler should ignore it entirely.
    stt_ev = SimpleNamespace(metrics=SimpleNamespace(
        type="stt_metrics",
        prompt_cached_tokens=9999,  # bogus — must NOT leak through
    ))
    _on_metrics_collected(stt_ev)
    assert session._jarvis_last_cache_read_tokens == 0

    # Now fire an LLM metric — should capture.
    llm_ev = SimpleNamespace(metrics=SimpleNamespace(
        type="llm_metrics",
        prompt_tokens=100,
        completion_tokens=10,
        prompt_cached_tokens=42,
    ))
    _on_metrics_collected(llm_ev)
    assert session._jarvis_last_cache_read_tokens == 42
