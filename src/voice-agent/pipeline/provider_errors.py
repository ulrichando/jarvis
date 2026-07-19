"""Provider-error classifier — turn a raw LLM / TTS / STT provider exception
into an explicit, human category + a plain sentence JARVIS can *say*, instead
of surfacing a bare "HTTP 400" (or nothing at all).

Motivation (2026-07-02): a provider failure used to reach the user as silence
(the session-close watchdog returned quietly) or an opaque status code. This
maps the error to a category the user actually cares about — "I'm out of
credits on Claude" — across the Anthropic / OpenAI / DeepSeek / Gemini
SDK error shapes, whose reprs all carry the provider's billing / rate-limit /
auth wording.

Pure + dependency-free: matches on ``status_code`` + the error's string repr.
Consumed by the session error/close handlers in jarvis_agent.py (spoken via
``session.say`` + a desktop notification) and *supersedes* the ad-hoc
``_UNRECOVERABLE_LLM_ERR_RE`` restart gate — ``ClassifiedError.recoverable`` is
now the single source of truth for "can a voice-client restart heal this?".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["ClassifiedError", "classify_provider_error"]


@dataclass(frozen=True)
class ClassifiedError:
    category: str          # machine tag, e.g. "out_of_credits"
    recoverable: bool      # can a voice-client restart / retry heal it?
    provider: str          # friendly provider name, e.g. "Claude"
    spoken: str            # ONE plain sentence for session.say() (LLM/STT only)
    notify_title: str      # desktop-notification title
    notify_body: str       # desktop-notification body


# ── provider friendly-name detection (from the model id or the error text) ───
_PROVIDER_PATS = (
    (re.compile(r"claude|anthropic", re.I), "Claude"),
    (re.compile(r"\bgpt|openai|\bo[134]\b|gpt-image|dall", re.I), "OpenAI"),
    (re.compile(r"deepseek", re.I), "DeepSeek"),
    (re.compile(r"gemini|google|imagen", re.I), "Gemini"),
    (re.compile(r"kimi|moonshot", re.I), "Kimi"),
    (re.compile(r"qwen", re.I), "Qwen"),
    (re.compile(r"openrouter", re.I), "OpenRouter"),
)


# ── local-LLM attribution (2026-07-11 mislabel fix) ──────────────────────────
# In LOCAL voice mode the supervisor LLM is an Ollama model served through
# livekit's OpenAI-COMPATIBLE plugin (lk_openai.LLM(base_url=<ollama>)) — so
# the error repr / rung label carries "openai" tokens that are the WIRE SHAPE,
# not the vendor. Live bug: an Ollama context overflow
# ("exceed_context_size_error") reached _detect_provider, the OpenAI pattern
# matched the plugin path in the TEXT, and JARVIS spoke "I can't reach OpenAI —
# looks like a network issue" for an on-device model that overflowed its
# window. For the local case, provider is family-named from the MODEL ID ONLY
# (never the text), and OpenAI is never named.
#
# Ollama/local signals in the ERROR TEXT — none of these appear in real
# OpenAI / DeepSeek / Anthropic error bodies (the bare plugin path
# "livekit.plugins.openai" alone is NOT a signal: the same plugin fronts the
# real OpenAI API):
_LOCAL_LLM_TEXT_RE = re.compile(
    r"exceed_context_size_error|exceeds the available context size"
    r"|\bollama\b|:11434\b|\bn_ctx\b|llama[._-]?cpp",
    re.I,
)
# Local/ollama MODEL-ID shapes: an explicit ollama/local prefix, or the bare
# ollama name:tag form (e.g. "qwen3:4b-instruct-2507" — no cloud id in
# SPEECH_MODELS uses a colon; _active_voice_model returns "ollama/<tag>" in
# local mode).
_LOCAL_LLM_MODEL_RE = re.compile(
    r"\bollama\b|^local[:/]|llama[._-]?cpp|^[\w.-]+:[\w.-]+$",
    re.I,
)


def _is_local_llm(model: str | None, text: str) -> bool:
    if model and _LOCAL_LLM_MODEL_RE.search(model):
        return True
    return _LOCAL_LLM_TEXT_RE.search(text) is not None


# The livekit OpenAI-COMPATIBLE plugin's class path is a WIRE SHAPE, not a
# vendor signal — DeepSeek / Kimi / OpenRouter all front through it, so their
# error reprs carry "livekit.plugins.openai.llm.LLM". Strip it before
# text-based provider detection so a DeepSeek/Kimi outage is never read as
# OpenAI. Real OpenAI errors carry other tokens (api.openai.com, gpt-*,
# insufficient_quota) that survive this.
_OPENAI_PLUGIN_PATH_RE = re.compile(r"livekit\.plugins\.openai[\w.]*", re.I)


def _detect_provider(model: str | None, text: str) -> str:
    if _is_local_llm(model, text):
        # Local (Ollama / llama.cpp) LLM behind the OpenAI-compatible plugin:
        # family-name from the model TAG when it looks local (ollama/qwen3:14b
        # → "Qwen"); with a stale/absent pin (text-signal detection) stay
        # generic. NEVER name OpenAI here — the "openai" token in the repr is
        # the plugin class name, and NEVER match the text (it carries that
        # token).
        if model and _LOCAL_LLM_MODEL_RE.search(model):
            for pat, name in _PROVIDER_PATS:
                if name != "OpenAI" and pat.search(model):
                    return name
        return "the local model"
    # The MODEL ID is authoritative — it names the real vendor even when the
    # error text carries only the openai-compat plugin's wire-shape "openai"
    # token. DeepSeek / Kimi / OpenRouter all front through
    # livekit.plugins.openai.LLM, so a ConnectError for a DeepSeek outage reads
    # "all LLMs failed (['livekit.plugins.openai.llm.LLM', ...])" — matching the
    # text there mislabels DeepSeek as OpenAI (live bug 2026-07-15). Prefer the
    # model, then fall back to the text with the bare plugin path stripped so it
    # never counts as an OpenAI signal (a real OpenAI error still carries
    # "api.openai.com" / "insufficient_quota" / a "gpt-*" id, which survive).
    if model:
        for pat, name in _PROVIDER_PATS:
            if pat.search(model):
                return name
    text = _OPENAI_PLUGIN_PATH_RE.sub(" ", text)
    for pat, name in _PROVIDER_PATS:
        if pat.search(text):
            return name
    return "the model provider"


# ── STT-component attribution (2026-07-10 mislabel fix) ──────────────────────
# An STT failure is NEVER the speech-LLM's fault. Before this, a local
# faster-whisper CUDA blip was classified with model=~/.jarvis/voice-model
# (a possibly STALE cloud pin like "deepseek-chat-v3") → _detect_provider
# matched "deepseek" → the user was told "I can't reach DeepSeek — looks like
# a network issue" for an on-device GPU error. For component=="stt" the
# provider is detected from the ERROR TEXT ONLY (the livekit STTError repr
# carries the rung label, e.g. "local:faster-whisper/large-v3-turbo").
_STT_PROVIDER_PATS = (
    (re.compile(r"deepgram", re.I), "Deepgram"),
    (re.compile(r"faster.?whisper|ctranslate|local:", re.I), "the local speech engine"),
)


def _detect_stt_provider(text: str) -> str:
    for pat, name in _STT_PROVIDER_PATS:
        if pat.search(text):
            return name
    return "the speech engine"


# Names the ON-DEVICE STT engine in an error repr that carries NO structured
# component tag. FasterWhisperSTT._recognize_impl raises a bare
# ``APIConnectionError("faster-whisper local STT failed: …")`` (no ``.type``,
# no "stt_error" substring); that shape reaches the close-watchdog call site,
# which passes NO component — see ``_infer_component``.
_STT_TEXT_RE = re.compile(r"faster.?whisper|ctranslate|local STT", re.I)


# Transient GPU markers (kept in sync with providers/faster_whisper_stt.py's
# in-rung retry regex): ctranslate2 CUDA failures — a wedged GPU context
# (cuInit 999 after a suspend / driver hiccup) or, on a shared card, an OOM.
_STT_GPU_ERR_RE = re.compile(r"parallel_for failed|\bcuda|cublas|cudnn", re.I)


def _infer_component(err: object, text: str, default: str) -> str:
    """Sharpen the caller-supplied component from the error itself. livekit's
    session ErrorEvent / CloseEvent carry pydantic STTError / TTSError /
    LLMError models whose ``type`` field tags the failing component — the
    close-watchdog call sites pass no component, which used to default an STT
    session-death to "llm" and let the LLM pin name the provider."""
    t = getattr(err, "type", None)
    if isinstance(t, str):
        for comp in ("stt", "tts", "llm"):
            if t == f"{comp}_error":
                return comp
    if "stt_error" in text:
        return "stt"
    if "tts_error" in text:
        return "tts"
    # Bare local-STT rung failure (no .type / no "stt_error" tag) — the shape
    # FasterWhisperSTT actually raises, which reaches the close-watchdog with
    # no component. Without this it defaults to "llm" and the GPU blip is
    # voiced against the stale cloud pin ("I can't reach DeepSeek — network").
    if _STT_TEXT_RE.search(text):
        return "stt"
    return default


# ── category detection (ORDERED: specific → generic; first match wins) ───────
# Each entry: (category, status_codes, message_regex_or_None).
_RULES: tuple[tuple[str, frozenset[int], "re.Pattern[str] | None"], ...] = (
    # Out of money — needs credits, a restart can NEVER heal it. Matches
    # Anthropic "credit balance is too low", OpenAI "insufficient_quota",
    # Stripe-style "payment required", HTTP 402.
    ("out_of_credits", frozenset({402}),
     re.compile(r"credit balance|insufficient(?!\s+permission)|billing|payment required|out of credit|not enough", re.I)),
    # Prompt too large for the window (a restart clears context, so recoverable).
    # Ollama/llama.cpp phrase the overflow as "request (N tokens) exceeds the
    # available context size (M tokens)" + type "exceed_context_size_error" —
    # matched here (BEFORE the network rule) so a local overflow is never
    # swallowed by the network regex via the APIConnection wrapper repr.
    ("context_too_long", frozenset(),
     re.compile(r"context length|maximum context|context_length_exceeded|prompt is too long|too many tokens|reduce the length|maximum.{0,20}tokens|exceed_context_size_error|exceeds the available context size|available context size|context size", re.I)),
    # Bad / missing / unauthorized key — needs a real key, restart can't heal.
    ("auth_invalid", frozenset({401, 403}),
     re.compile(r"invalid.{0,15}api.?key|incorrect api key|no api key|missing.{0,10}api.?key|api.?key.{0,20}(missing|invalid|not)|authentication|unauthorized|permission denied|access denied|invalid x-api-key", re.I)),
    # Usage quota exhausted (monthly/tier) — distinct from a transient 429.
    ("quota_exceeded", frozenset(),
     re.compile(r"quota|usage limit|monthly limit|exceeded your current", re.I)),
    # Transient throttle — recovers with time (restart+retry eventually works).
    ("rate_limited", frozenset({429}),
     re.compile(r"rate.?limit|too many requests|slow down", re.I)),
    # Model missing / overloaded — transient or a config typo; restart-safe.
    ("model_unavailable", frozenset({404, 529}),
     re.compile(r"model not found|model_not_found|does not exist|no such model|overloaded|model.{0,15}(unavailable|not available)|not_found_error", re.I)),
    ("timeout", frozenset({408, 504}),
     re.compile(r"timeout|timed out|deadline exceeded", re.I)),
    ("network", frozenset(),
     re.compile(r"connection error|connection refused|econnrefused|network is unreachable|unreachable|failed to (connect|establish)|getaddrinfo|name resolution|apiconnection", re.I)),
    ("server_error", frozenset({500, 502, 503}),
     re.compile(r"internal server error|bad gateway|service unavailable|gateway timeout|server had an error", re.I)),
    # Generic 400 that matched none of the above — still nicer than "HTTP 400".
    ("bad_request", frozenset({400}), None),
)

# A restart CANNOT fix these — the user must add credits / fix the key / wait
# for a quota reset. Everything else is treated as recoverable (transient crash,
# throttle, overload, or a context reset that a fresh session clears).
_NON_RECOVERABLE = frozenset({"out_of_credits", "auth_invalid", "quota_exceeded"})


def _spoken_and_notify(category: str, provider: str, component: str) -> tuple[str, str, str]:
    """(spoken, notify_title, notify_body) for a category.

    ``spoken`` is LLM/STT-phrased (first person); TTS callers ignore it — you
    cannot *speak* an error when speech synthesis itself is what broke.
    ``component`` ∈ {"llm","tts","stt"} tailors the notification wording.
    """
    where = {"tts": "speech synthesis", "stt": "speech recognition"}.get(component, "")
    where_sfx = f" ({where})" if where else ""
    table = {
        "out_of_credits": (
            f"I'm out of credits on {provider}. Add credits, or switch models in the tray.",
            f"JARVIS — out of credits on {provider}",
            f"{provider} rejected the request: the account is out of credits / balance is too low{where_sfx}. Add funds, or switch the model in the tray (~/.jarvis/voice-model).",
        ),
        "quota_exceeded": (
            f"I've hit the usage quota on {provider}. Switch models, or wait for the quota to reset.",
            f"JARVIS — {provider} quota exhausted",
            f"{provider} usage quota is exhausted{where_sfx}. Switch models in the tray or wait for the reset.",
        ),
        "auth_invalid": (
            f"My {provider} API key is invalid or missing. The key needs fixing.",
            f"JARVIS — {provider} API key invalid/missing",
            f"{provider} rejected auth{where_sfx}: the API key is invalid, missing, or lacks permission. Check the key in ~/.jarvis/keys.env.",
        ),
        "rate_limited": (
            f"I'm being rate-limited on {provider} right now. Give it a moment, or switch models.",
            f"JARVIS — {provider} rate-limited",
            f"{provider} is throttling requests{where_sfx} (HTTP 429). Transient — it recovers on its own; switch models to keep going.",
        ),
        "context_too_long": (
            f"This conversation got too long for {provider}. I need to compact or start fresh.",
            f"JARVIS — context too long for {provider}",
            f"The prompt exceeded {provider}'s context window{where_sfx}. A fresh session clears it; token-aware pruning should normally prevent this.",
        ),
        "model_unavailable": (
            f"The {provider} model is unavailable right now. It's usually transient.",
            f"JARVIS — {provider} model unavailable",
            f"{provider} says the model is unavailable / overloaded{where_sfx}. Usually transient; a fallback rung or retry recovers it.",
        ),
        "timeout": (
            f"I'm having trouble reaching {provider} — the request timed out.",
            f"JARVIS — {provider} timed out",
            f"The {provider} request timed out{where_sfx}. Usually transient provider-side load; a retry recovers it.",
        ),
        "network": (
            f"I can't reach {provider} — looks like a network issue.",
            f"JARVIS — can't reach {provider}",
            f"Network error reaching {provider}{where_sfx} (connection refused / unreachable). Check connectivity; it retries automatically.",
        ),
        "stt_gpu": (
            "My local speech-to-text hit a GPU error. I'll keep retrying — "
            "if I keep missing you, the GPU may need a recovery.",
            "JARVIS — local speech-to-text GPU error",
            "Local faster-whisper hit a transient CUDA/GPU error (the "
            "on-device GPU context wedged) — NOT a cloud provider or network "
            "issue. It retries automatically and falls back to CPU so speech "
            "keeps working; if it persists, run bin/jarvis-cuda-recover to "
            "reset the GPU, or set JARVIS_LOCAL_STT_DEVICE=cpu.",
        ),
        "server_error": (
            f"{provider} is having server trouble. It should recover shortly.",
            f"JARVIS — {provider} server error",
            f"{provider} returned a server error (5xx){where_sfx}. Provider-side; a retry / fallback rung recovers it.",
        ),
        "bad_request": (
            f"I hit a request error with {provider}. It may be transient.",
            f"JARVIS — {provider} rejected the request",
            f"{provider} rejected the request (HTTP 400){where_sfx} for a reason other than billing/auth/rate-limit. Often transient provider-side.",
        ),
        "unknown": (
            f"I ran into an error with {provider}.",
            f"JARVIS — {provider} error",
            f"Unclassified {provider} error{where_sfx}.",
        ),
    }
    return table.get(category, table["unknown"])


def classify_provider_error(
    err: object,
    *,
    model: str | None = None,
    component: str = "llm",
) -> ClassifiedError:
    """Classify a provider exception into a speakable category + messages.

    ``err`` — the exception (any provider SDK; matched on ``status_code`` +
    ``str(err)``). ``model`` — the model id in play (sharpens provider
    detection + wording). ``component`` — {"llm","tts","stt"} (notification
    wording; TTS ignores ``spoken``).
    """
    text = str(err) or ""
    type_name = type(err).__name__
    haystack = f"{type_name} {text}"
    status = getattr(err, "status_code", None)
    if not isinstance(status, int):
        status = None

    component = _infer_component(err, haystack, component)
    if component == "stt":
        # Never attribute an STT failure to the (possibly stale) LLM model
        # pin — text-only detection so the alert names the actual speech
        # engine, not a cloud LLM that was never in play.
        provider = _detect_stt_provider(haystack)
    else:
        provider = _detect_provider(model, haystack)

    category = "unknown"
    if component == "stt" and _STT_GPU_ERR_RE.search(haystack):
        # Local STT GPU/CUDA error — report it as exactly that, not as a
        # provider "network" failure ("apiconnection" in the wrapped repr
        # used to win the category and read as "can't reach <provider>").
        category = "stt_gpu"
    else:
        for cat, codes, pat in _RULES:
            code_hit = status is not None and status in codes
            msg_hit = pat is not None and pat.search(haystack) is not None
            if code_hit or msg_hit:
                category = cat
                break
        else:
            # Type-name fallbacks for SDK errors whose repr lacks keywords.
            if "Timeout" in type_name:
                category = "timeout"
            elif "Connection" in type_name:
                category = "network"

    spoken, title, body = _spoken_and_notify(category, provider, component)
    # Append a short raw tail to the notification body for the unclassified /
    # generic cases so a debugging human still sees the underlying error.
    if category in ("unknown", "bad_request"):
        detail = text[:160].strip()
        if detail:
            body = f"{body}\nDetail: {detail}"

    return ClassifiedError(
        category=category,
        recoverable=category not in _NON_RECOVERABLE,
        provider=provider,
        spoken=spoken,
        notify_title=title,
        notify_body=body,
    )
