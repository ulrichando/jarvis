"""Tests for pipeline.provider_errors.classify_provider_error."""
from __future__ import annotations

import pytest

from pipeline.provider_errors import classify_provider_error


class _Err(Exception):
    """Fake provider SDK error: status_code attr + a message repr."""

    def __init__(self, msg: str, status_code: int | None = None):
        super().__init__(msg)
        if status_code is not None:
            self.status_code = status_code


# (label, error, model, expected_category, expected_recoverable)
CASES = [
    # The incident that started this: Anthropic 400 "credit balance too low".
    ("anthropic_credits",
     _Err("Error code: 400 - {'type':'invalid_request_error','message':'Your credit balance is too low to access the Anthropic API.'}", 400),
     "claude-haiku-4-5", "out_of_credits", False),
    # OpenAI insufficient_quota (means out of credits / billing).
    ("openai_insufficient_quota",
     _Err("Error code: 429 - {'error':{'code':'insufficient_quota','message':'You exceeded your current quota, please check your plan and billing details.'}}", 429),
     "gpt-4o", "out_of_credits", False),
    ("http_402",
     _Err("Payment Required", 402), "deepseek-chat", "out_of_credits", False),
    # Context window overflow → recoverable (a fresh session clears context).
    ("context_length",
     _Err("This model's maximum context length is 200000 tokens, however you requested 250000.", 400),
     "claude-sonnet-4-6", "context_too_long", True),
    # Auth: invalid / missing key, 401/403.
    ("invalid_key",
     _Err("Error code: 401 - {'error':{'message':'invalid x-api-key','type':'authentication_error'}}", 401),
     "claude-haiku-4-5", "auth_invalid", False),
    ("forbidden",
     _Err("403 Forbidden: access denied", 403), "gpt-4o", "auth_invalid", False),
    # Pure quota wording (not billing) → quota_exceeded, non-recoverable.
    ("monthly_quota",
     _Err("Rate limit reached: you have exceeded your monthly limit / usage limit for this model.", 429),
     "gpt-4o-mini", "quota_exceeded", False),
    # Transient 429 rate limit (no quota/billing wording) → recoverable.
    ("rate_limit_transient",
     _Err("429 Too Many Requests: rate_limit_exceeded, please slow down", 429),
     "deepseek-v4-flash", "rate_limited", True),
    # Model unavailable / overloaded → recoverable.
    ("overloaded",
     _Err("Error code: 529 - {'type':'overloaded_error','message':'Overloaded'}", 529),
     "claude-sonnet-4-6", "model_unavailable", True),
    ("model_not_found",
     _Err("The model `foo-bar` does not exist or you do not have access to it.", 404),
     "foo-bar", "model_unavailable", True),
    # Timeout / network → recoverable.
    ("timeout",
     _Err("Request timed out.", None), "claude-haiku-4-5", "timeout", True),
    ("connection",
     _Err("Connection error: [Errno 111] Connection refused", None), "gpt-4o", "network", True),
    ("server_5xx",
     _Err("502 Bad Gateway", 502), "deepseek-chat", "server_error", True),
    # Generic 400 with no billing/auth/context wording → bad_request (recoverable).
    ("generic_400",
     _Err("400 - {'message':'something odd'}", 400), "claude-haiku-4-5", "bad_request", True),
    # Nothing matches → unknown (recoverable).
    ("unknown",
     _Err("kaboom"), "claude-haiku-4-5", "unknown", True),
]


@pytest.mark.parametrize("label,err,model,cat,recoverable", CASES, ids=[c[0] for c in CASES])
def test_category_and_recoverability(label, err, model, cat, recoverable):
    c = classify_provider_error(err, model=model)
    assert c.category == cat, f"{label}: got {c.category!r} for {err!r}"
    assert c.recoverable is recoverable, f"{label}: recoverable={c.recoverable}"


def test_provider_detected_from_model():
    assert classify_provider_error(_Err("boom"), model="claude-haiku-4-5").provider == "Claude"
    assert classify_provider_error(_Err("boom"), model="deepseek-v4-flash").provider == "DeepSeek"
    assert classify_provider_error(_Err("boom"), model="gpt-4o").provider == "OpenAI"


def test_provider_detected_from_error_text_when_model_unknown():
    c = classify_provider_error(_Err("anthropic: credit balance too low", 400))
    assert c.provider == "Claude"
    assert c.category == "out_of_credits"


def test_out_of_credits_speaks_credits_not_http():
    c = classify_provider_error(
        _Err("Your credit balance is too low", 400), model="claude-haiku-4-5"
    )
    # The spoken line must be human ("credits"/"Claude"), never "HTTP 400".
    assert "credit" in c.spoken.lower()
    assert "Claude" in c.spoken
    assert "400" not in c.spoken
    assert c.recoverable is False


def test_timeout_type_name_fallback():
    # An error whose message lacks keywords but whose TYPE says Timeout.
    class ReadTimeout(Exception):
        pass

    c = classify_provider_error(ReadTimeout("x"), model="gpt-4o")
    assert c.category == "timeout"


def test_tts_component_shapes_notify_body():
    # Local Kokoro isn't in _PROVIDER_PATS (no cloud billing to name), so an
    # unmatched TTS model falls through to the generic provider string. The
    # tts component shaping is what this test guards.
    c = classify_provider_error(_Err("429 rate limit", 429), model="kokoro", component="tts")
    assert c.provider == "the model provider"
    assert "speech synthesis" in c.notify_body
    # spoken is still populated but TTS callers ignore it (can't speak if TTS broke).
    assert c.spoken


def test_generic_400_includes_raw_detail_for_debugging():
    c = classify_provider_error(_Err("400 - weird provider quirk xyz", 400), model="deepseek-chat")
    assert c.category == "bad_request"
    assert "xyz" in c.notify_body  # raw tail preserved for a human


# ── STT-component attribution (2026-07-10 "can't reach DeepSeek" mislabel) ───
#
# The live bug: local faster-whisper raised a transient CUDA error, the
# session died with a livekit STTError, and classify_provider_error attributed
# it to the stale ~/.jarvis/voice-model pin ("deepseek-chat-v3") → the user
# was told "I can't reach DeepSeek — looks like a network issue" for an
# on-device GPU failure. STT errors must NEVER name a cloud LLM provider.

class _LkSttErr:
    """Shape of livekit.agents.voice.events.STTError as seen by the close
    watchdog: a pydantic model with type='stt_error' whose str() carries the
    rung label + the wrapped exception repr."""

    type = "stt_error"

    def __init__(self, inner: str):
        self._inner = inner

    def __str__(self):
        return (
            "type='stt_error' timestamp=1760000000.0 "
            "label='providers.faster_whisper_stt.FasterWhisperSTT' "
            f"error=APIConnectionError(\"{self._inner}\") recoverable=False"
        )


_CUDA_STT = _LkSttErr(
    "faster-whisper local STT failed: parallel_for failed: "
    "cudaErrorInvalidDevice: invalid device ordinal"
)


def test_stt_cuda_error_is_stt_gpu_not_network():
    # The exact live shape: session-close watchdog passes NO component and the
    # stale cloud pin as model. Must classify as a local STT GPU error.
    c = classify_provider_error(_CUDA_STT, model="deepseek-chat-v3")
    assert c.category == "stt_gpu"
    assert c.recoverable is True  # a voice-client restart / retry heals it


def test_stt_error_never_attributed_to_cloud_llm_provider():
    c = classify_provider_error(_CUDA_STT, model="deepseek-chat-v3")
    for field in (c.provider, c.spoken, c.notify_title, c.notify_body):
        assert "DeepSeek" not in field
        assert "Claude" not in field
    assert "GPU" in c.notify_title or "GPU" in c.notify_body
    assert "speech-to-text" in c.spoken


def test_stt_component_explicit_still_ignores_llm_model():
    # The on("error") handler passes component="stt" explicitly.
    err = _Err("faster-whisper local STT failed: something broke")
    c = classify_provider_error(err, model="deepseek-v4-flash", component="stt")
    assert c.provider == "the local speech engine"
    assert "DeepSeek" not in c.spoken


def test_stt_non_cuda_error_keeps_normal_categories():
    # A Deepgram (cloud STT) network failure: still 'network', still named
    # after the STT provider — not the LLM pin, not stt_gpu.
    err = _LkSttErr("deepgram websocket connection refused")
    c = classify_provider_error(err, model="deepseek-v4-flash")
    assert c.category == "network"
    assert c.provider == "Deepgram"


def test_cuda_wording_in_llm_error_does_not_hit_stt_gpu():
    # stt_gpu is gated on the STT component — an LLM error mentioning cuda
    # (e.g. an ollama server-side crash) keeps the normal rules.
    err = _Err("ollama runner crashed: CUDA error: out of memory", 500)
    c = classify_provider_error(err, model="ollama/qwen2.5:7b", component="llm")
    assert c.category == "server_error"
    assert c.provider == "Qwen"


def test_raw_untagged_faster_whisper_error_is_stt_gpu():
    # REGRESSION (residual mislabel): the shape FasterWhisperSTT actually
    # raises is a BARE APIConnectionError — no `.type`, no "stt_error" tag —
    # which reaches the close-watchdog with NO component + a stale cloud pin.
    # The tagged-STTError path (_CUDA_STT above) was fixed first; this untagged
    # rung shape still classified as 'network' → "I can't reach DeepSeek".
    from livekit.agents import APIConnectionError
    err = APIConnectionError(
        "faster-whisper local STT failed: parallel_for failed: "
        "cudaErrorInvalidDevice: invalid device ordinal"
    )
    assert getattr(err, "type", None) != "stt_error"  # genuinely untagged
    c = classify_provider_error(err, model="deepseek-chat-v3")  # no component
    assert c.category == "stt_gpu"
    assert c.recoverable is True
    for field in (c.provider, c.spoken, c.notify_title):
        assert "DeepSeek" not in field


@pytest.mark.parametrize("marker", ["parallel_for failed", "cuda", "cublas", "cudnn"])
def test_all_gpu_markers_route_untagged_stt_error_to_stt_gpu(marker):
    # Pin the full transient-GPU marker set on the untagged rung shape — a
    # regex edit dropping cublas/cudnn (real ctranslate2/cuDNN failures) would
    # silently regress attribution back to the cloud pin.
    from livekit.agents import APIConnectionError
    err = APIConnectionError(f"faster-whisper local STT failed: {marker} error")
    c = classify_provider_error(err, model="deepseek-chat-v3")
    assert c.category == "stt_gpu"


# ── HARD constraint: the cloud/online path is byte-identical ─────────────────

def test_cloud_deepseek_out_of_credits_still_says_deepseek():
    # A REAL DeepSeek billing failure (component=llm, deepseek pin) must
    # still be attributed to DeepSeek, spoken as credits, non-recoverable —
    # exactly as before the STT mislabel fix.
    c = classify_provider_error(
        _Err("Error code: 402 - Insufficient Balance", 402),
        model="deepseek-v4-flash",
    )
    assert c.category == "out_of_credits"
    assert c.provider == "DeepSeek"
    assert "DeepSeek" in c.spoken and "credit" in c.spoken.lower()
    assert c.recoverable is False


def test_cloud_llm_network_error_unchanged():
    c = classify_provider_error(
        _Err("Connection error: [Errno 111] Connection refused"),
        model="deepseek-v4-flash",
    )
    assert c.category == "network"
    assert c.provider == "DeepSeek"
    assert "can't reach DeepSeek" in c.spoken


# ── local-LLM (Ollama) attribution (2026-07-11 "can't reach OpenAI" mislabel) ─
#
# The live bug: LOCAL voice mode, supervisor = qwen3-4b via Ollama through
# livekit's OpenAI-COMPATIBLE plugin (lk_openai.LLM(base_url=<ollama>)). A
# context overflow came back as
#   APIStatusError: Error code: 400 - request (39648 tokens) exceeds the
#   available context size (4096 tokens), try increasing it ...
#   "type":"exceed_context_size_error"
# and JARVIS spoke "I can't reach OpenAI — looks like a network issue" —
# wrong provider (the "openai" token is the plugin class path, not the
# vendor) AND wrong category (context overflow, not connectivity).

class APIStatusError(Exception):
    """Named like the openai-SDK error the livekit plugin re-raises, so the
    type-name lands in the classifier haystack exactly as live."""

    def __init__(self, msg: str, status_code: int = 400):
        super().__init__(msg)
        self.status_code = status_code


# The exact live wording (Ollama's /v1 chat endpoint).
_OLLAMA_CTX_MSG = (
    'Error code: 400 - {"error":{"message":"request (39648 tokens) exceeds '
    'the available context size (4096 tokens), try increasing it",'
    '"type":"exceed_context_size_error","param":null,"code":null}}'
)


class _LkLlmErr:
    """Shape of livekit.agents.voice.events LLMError as seen by the close
    watchdog: type='llm_error', str() carries the plugin label
    ('livekit.plugins.openai.llm.LLM') + the wrapped exception repr."""

    type = "llm_error"

    def __init__(self, inner: str):
        self._inner = inner

    def __str__(self):
        return (
            "type='llm_error' timestamp=1760000000.0 "
            "label='livekit.plugins.openai.llm.LLM' "
            f"error=APIStatusError('{self._inner}') recoverable=False"
        )


def _assert_no_mislabel_words(c):
    """Neither the provider nor any user-facing string may claim OpenAI, a
    network problem, or a billing problem for a local context overflow."""
    assert c.provider != "OpenAI"
    for field in (c.spoken, c.notify_title, c.notify_body):
        low = field.lower()
        assert "openai" not in low, field
        assert "network" not in low, field
        assert "credit" not in low, field


def test_local_ollama_context_overflow_is_context_too_long_not_network():
    # The exact live shape: local mode passes model="ollama/<tag>".
    c = classify_provider_error(
        APIStatusError(_OLLAMA_CTX_MSG),
        model="ollama/qwen3:4b-instruct-2507",
    )
    assert c.category == "context_too_long"
    assert c.recoverable is True  # a restart / prune clears the context
    _assert_no_mislabel_words(c)
    # Family-named from the local tag, never from the plugin token.
    assert c.provider == "Qwen"


def test_local_overflow_wrapped_llm_error_with_no_model_names_local():
    # Close-watchdog shape: the pydantic LLMError repr carries the
    # 'livekit.plugins.openai' plugin path; the pin is unreadable (model=None).
    c = classify_provider_error(_LkLlmErr(_OLLAMA_CTX_MSG), model=None)
    assert c.category == "context_too_long"
    assert c.provider == "the local model"
    _assert_no_mislabel_words(c)
    assert "too long" in c.spoken


def test_local_overflow_with_stale_cloud_pin_never_names_cloud():
    # Text says Ollama (exceed_context_size_error) but the pin is a stale
    # cloud id — same stale-pin trap the 2026-07-10 STT fix closed. The
    # ollama-specific wording is authoritative: never blame DeepSeek/OpenAI.
    c = classify_provider_error(APIStatusError(_OLLAMA_CTX_MSG), model="deepseek-chat-v3")
    assert c.category == "context_too_long"
    assert c.provider == "the local model"
    for field in (c.provider, c.spoken, c.notify_title, c.notify_body):
        assert "DeepSeek" not in field
    _assert_no_mislabel_words(c)


def test_local_ollama_server_down_never_names_openai():
    # Ollama genuinely unreachable → still 'network', but named after the
    # local model — the OpenAI-compatible base_url must not flip the vendor.
    c = classify_provider_error(
        _Err("APIConnectionError: Connection refused: http://127.0.0.1:11434/v1"),
        model="ollama/qwen3:14b",
    )
    assert c.category == "network"
    assert c.provider == "Qwen"
    assert "OpenAI" not in c.spoken and "openai" not in c.notify_body.lower()


# ── HARD constraint: real cloud-OpenAI classification is byte-identical ──────

def test_real_openai_network_error_still_network_openai():
    c = classify_provider_error(
        _Err("Connection error: [Errno 111] Connection refused (api.openai.com)"),
        model="gpt-4o",
    )
    assert c.category == "network"
    assert c.provider == "OpenAI"
    assert "can't reach OpenAI" in c.spoken


def test_real_openai_context_overflow_still_names_openai():
    c = classify_provider_error(
        _Err("This model's maximum context length is 128000 tokens, however you requested 200000 tokens.", 400),
        model="gpt-4o",
    )
    assert c.category == "context_too_long"
    assert c.provider == "OpenAI"
    assert "OpenAI" in c.spoken


# ── DeepSeek-via-openai-plugin mislabel (live bug 2026-07-15) ─────────────────
# DeepSeek/Kimi/OpenRouter all front through livekit.plugins.openai.LLM, so a
# ConnectError from the fallback cascade reads "all LLMs failed
# (['livekit.plugins.openai.llm.LLM', ...])". The bare plugin path tripped the
# OpenAI pattern in the error TEXT → JARVIS spoke "I can't reach OpenAI" for a
# DeepSeek network blip. The model id (the active pin) is authoritative.
def test_deepseek_connecterror_via_openai_plugin_names_deepseek_not_openai():
    from livekit.agents import APIConnectionError
    err = APIConnectionError(
        "all LLMs failed (['livekit.plugins.openai.llm.LLM', "
        "'livekit.plugins.openai.llm.LLM'])"
    )
    c = classify_provider_error(err, model="deepseek-chat-v3", component="llm")
    assert c.provider == "DeepSeek"
    for field in (c.provider, c.spoken, c.notify_title, c.notify_body):
        assert "OpenAI" not in field


def test_openai_plugin_path_alone_is_not_an_openai_signal_when_model_unknown():
    # No pin: the bare plugin path must NOT read as OpenAI — stay generic
    # rather than name the wrong vendor.
    from livekit.agents import APIConnectionError
    err = APIConnectionError("all LLMs failed (['livekit.plugins.openai.llm.LLM'])")
    c = classify_provider_error(err, component="llm")
    assert "OpenAI" not in c.provider


def test_real_openai_outage_still_detected_from_url_token():
    # A genuine OpenAI failure carries api.openai.com even with no model pin —
    # that token survives the plugin-path strip, so OpenAI is still named.
    c = classify_provider_error(
        _Err("Connection error to https://api.openai.com/v1/chat/completions", None),
    )
    assert c.provider == "OpenAI"
