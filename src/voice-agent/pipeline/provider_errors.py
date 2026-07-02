"""Provider-error classifier — turn a raw LLM / TTS / STT provider exception
into an explicit, human category + a plain sentence JARVIS can *say*, instead
of surfacing a bare "HTTP 400" (or nothing at all).

Motivation (2026-07-02): a provider failure used to reach the user as silence
(the session-close watchdog returned quietly) or an opaque status code. This
maps the error to a category the user actually cares about — "I'm out of
credits on Claude" — across the Anthropic / OpenAI / DeepSeek / Groq / Gemini
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
    (re.compile(r"groq|orpheus|\bllama|whisper", re.I), "Groq"),
    (re.compile(r"gemini|google|imagen", re.I), "Gemini"),
    (re.compile(r"kimi|moonshot", re.I), "Kimi"),
    (re.compile(r"qwen", re.I), "Qwen"),
    (re.compile(r"openrouter", re.I), "OpenRouter"),
)


def _detect_provider(model: str | None, text: str) -> str:
    for pat, name in _PROVIDER_PATS:
        if (model and pat.search(model)) or pat.search(text):
            return name
    return "the model provider"


# ── category detection (ORDERED: specific → generic; first match wins) ───────
# Each entry: (category, status_codes, message_regex_or_None).
_RULES: tuple[tuple[str, frozenset[int], "re.Pattern[str] | None"], ...] = (
    # Out of money — needs credits, a restart can NEVER heal it. Matches
    # Anthropic "credit balance is too low", OpenAI "insufficient_quota",
    # Stripe-style "payment required", HTTP 402.
    ("out_of_credits", frozenset({402}),
     re.compile(r"credit balance|insufficient(?!\s+permission)|billing|payment required|out of credit|not enough", re.I)),
    # Prompt too large for the window (a restart clears context, so recoverable).
    ("context_too_long", frozenset(),
     re.compile(r"context length|maximum context|context_length_exceeded|prompt is too long|too many tokens|reduce the length|maximum.{0,20}tokens", re.I)),
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

    provider = _detect_provider(model, haystack)

    category = "unknown"
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
