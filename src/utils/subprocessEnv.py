"""
Environment variable scrubbing for subprocess spawning.

When running inside CI/GitHub Actions, sensitive secrets are stripped
from subprocess environments to prevent prompt-injection exfiltration.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, Optional

# Env vars to strip from subprocess environments in CI contexts
_GHA_SUBPROCESS_SCRUB = [
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_CUSTOM_HEADERS",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RUNTIME_URL",
    "ALL_INPUTS",
    "OVERRIDE_GITHUB_TOKEN",
    "DEFAULT_WORKFLOW_TOKEN",
    "SSH_SIGNING_KEY",
]

_upstream_proxy_env_fn: Optional[Callable[[], Dict[str, str]]] = None


def register_upstream_proxy_env_fn(fn: Callable[[], Dict[str, str]]) -> None:
    """Register a function that provides proxy environment variables."""
    global _upstream_proxy_env_fn
    _upstream_proxy_env_fn = fn


def subprocess_env() -> Dict[str, str]:
    """
    Return a copy of os.environ with sensitive secrets stripped,
    for use when spawning subprocesses.

    Gated on CLAUDE_CODE_SUBPROCESS_ENV_SCRUB environment variable.
    """
    proxy_env = _upstream_proxy_env_fn() if _upstream_proxy_env_fn else {}

    scrub_enabled = os.environ.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "").lower() in (
        "1",
        "true",
        "yes",
    )

    if not scrub_enabled:
        if proxy_env:
            return {**os.environ, **proxy_env}
        return dict(os.environ)

    env = {**os.environ, **proxy_env}
    for key in _GHA_SUBPROCESS_SCRUB:
        env.pop(key, None)
        env.pop(f"INPUT_{key}", None)

    return env
