"""Managed environment variable filtering for settings-sourced env."""

from __future__ import annotations

import os
from typing import Optional

from .managedEnvConstants import SAFE_ENV_VARS, is_provider_managed_env_var


def _is_env_truthy(value: Optional[str]) -> bool:
    """Check if an env var value is truthy."""
    if not value:
        return False
    return value.lower() in ("1", "true", "yes")


def without_ssh_tunnel_vars(
    env: Optional[dict[str, str]],
) -> dict[str, str]:
    """Strip SSH tunnel auth vars when ANTHROPIC_UNIX_SOCKET is set."""
    if not env or not os.environ.get("ANTHROPIC_UNIX_SOCKET"):
        return env or {}
    tunnel_vars = {
        "ANTHROPIC_UNIX_SOCKET",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    }
    return {k: v for k, v in env.items() if k not in tunnel_vars}


def without_host_managed_provider_vars(
    env: Optional[dict[str, str]],
) -> dict[str, str]:
    """Strip provider-managed vars when host owns inference routing."""
    if not env:
        return {}
    if not _is_env_truthy(os.environ.get("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST")):
        return env
    return {k: v for k, v in env.items() if not is_provider_managed_env_var(k)}


def filter_settings_env(
    env: Optional[dict[str, str]],
) -> dict[str, str]:
    """Compose the strip filters applied to every settings-sourced env object."""
    return without_host_managed_provider_vars(without_ssh_tunnel_vars(env))


def filter_safe_env_vars(
    env: dict[str, str],
) -> dict[str, str]:
    """Return only safe env vars from the given dict."""
    return {k: v for k, v in env.items() if k.upper() in SAFE_ENV_VARS}
