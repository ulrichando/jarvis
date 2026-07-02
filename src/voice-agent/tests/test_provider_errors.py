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
    ("groq_quota",
     _Err("Rate limit reached: you have exceeded your monthly limit / usage limit for this model.", 429),
     "openai/gpt-oss-120b", "quota_exceeded", False),
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
    c = classify_provider_error(_Err("429 rate limit", 429), model="orpheus", component="tts")
    assert c.provider == "Groq"
    assert "speech synthesis" in c.notify_body
    # spoken is still populated but TTS callers ignore it (can't speak if TTS broke).
    assert c.spoken


def test_generic_400_includes_raw_detail_for_debugging():
    c = classify_provider_error(_Err("400 - weird provider quirk xyz", 400), model="deepseek-chat")
    assert c.category == "bad_request"
    assert "xyz" in c.notify_body  # raw tail preserved for a human
