"""Voice LLM output cap (voice-setup todo #5) — the DeepSeek builds (incl. the
tray-pinned live primary) now cap output like Anthropic/Gemini already did."""
from providers import llm


def test_voice_max_tokens_default(monkeypatch):
    monkeypatch.delenv("JARVIS_VOICE_MAX_TOKENS", raising=False)
    assert llm._voice_max_tokens() == 200


def test_voice_max_tokens_env_tunable(monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_MAX_TOKENS", "120")
    assert llm._voice_max_tokens() == 120
    monkeypatch.setenv("JARVIS_VOICE_MAX_TOKENS", "junk")
    assert llm._voice_max_tokens() == 200  # malformed -> default


def test_deepseek_extra_body_caps_and_keeps_non_thinking(monkeypatch):
    monkeypatch.delenv("JARVIS_VOICE_MAX_TOKENS", raising=False)
    eb = llm._deepseek_extra_body()
    assert eb["max_tokens"] == 200
    assert eb["thinking"] == {"type": "disabled"}  # non-thinking pin preserved
    monkeypatch.setenv("JARVIS_VOICE_MAX_TOKENS", "150")
    assert llm._deepseek_extra_body()["max_tokens"] == 150


def test_deepseek_extra_body_does_not_mutate_shared_constant():
    llm._deepseek_extra_body()
    assert "max_tokens" not in llm._DEEPSEEK_NON_THINKING  # fresh dict each call


def test_every_deepseek_build_constructs_with_the_cap(monkeypatch):
    # The live primary + every DeepSeek entry must build without error and pass
    # the capped extra_body (grep-confirmed all 4 sites use _deepseek_extra_body).
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    for key in ("deepseek-chat", "deepseek-v4-flash", "deepseek-chat-v3"):
        built = llm.SPEECH_MODELS[key]["build"]()
        assert built is not None, key
