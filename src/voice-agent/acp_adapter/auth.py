"""ACP auth methods for the JARVIS adapter.

JARVIS is a local-only agent: the user runs it on their own machine and
the IDE spawns the adapter over stdio. There is no per-user provisioning
flow — the supervisor LLMs read their API keys from ``.env`` /
``~/.jarvis/.env`` at startup, same as the voice agent does.

We still advertise at least one auth method because the upstream ACP
spec requires it. The default ``none`` method is a no-op the client
accepts to enter the session lifecycle.
"""

from __future__ import annotations

import os
from typing import Any


# Stable ids the adapter advertises. ``none`` is the no-op path used by
# Zed when JARVIS has provider keys configured outside the IDE. The
# terminal setup id is reserved for a future ``jarvis-acp --setup`` flow
# (not implemented yet — JARVIS uses .env files, not interactive setup).
NONE_AUTH_METHOD_ID = "none"
TERMINAL_SETUP_AUTH_METHOD_ID = "jarvis-setup"


def _has_any_supervisor_key() -> bool:
    """Return True when at least one supervisor LLM provider has credentials.

    Mirrors the gates in ``providers/llm.py`` — Anthropic / Groq /
    DeepSeek / OpenAI / OpenRouter / Google all populate the dispatcher.
    """
    for env_var in (
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GOOGLE_API_KEY",
    ):
        if os.environ.get(env_var, "").strip():
            return True
    return False


def has_provider() -> bool:
    """Return True when the supervisor LLM stack can be built."""
    return _has_any_supervisor_key()


def build_auth_methods() -> list[Any]:
    """Return ACP-shaped auth methods for the ``initialize`` response.

    Always returns at least one entry so spec-strict ACP clients (Zed in
    particular) accept the handshake. When no provider keys are present
    we still advertise ``none`` plus the terminal-setup hint, letting the
    user discover the adapter is alive even before they wire up keys.
    """
    from acp.schema import AuthMethodAgent, TerminalAuthMethod

    methods: list[Any] = [
        AuthMethodAgent(
            id=NONE_AUTH_METHOD_ID,
            name="Local credentials",
            description=(
                "Use the API keys configured in JARVIS's .env "
                "(no per-session login). The adapter reads ANTHROPIC_API_KEY, "
                "GROQ_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, "
                "OPENROUTER_API_KEY, and GOOGLE_API_KEY at startup."
            ),
        ),
    ]

    if not has_provider():
        methods.append(
            TerminalAuthMethod(
                id=TERMINAL_SETUP_AUTH_METHOD_ID,
                name="Configure JARVIS provider keys",
                description=(
                    "JARVIS has no LLM provider keys configured. Edit "
                    "src/voice-agent/.env (or export ANTHROPIC_API_KEY / "
                    "GROQ_API_KEY etc.) and reconnect."
                ),
                type="terminal",
                args=["--setup"],
            )
        )

    return methods
