"""Trust dialog utilities for terminal.

Risk assessment, trust level formatting, and security warning display
for evaluating tool call safety.
"""

from __future__ import annotations
from typing import Any, Optional
import os
import re

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Sensitive environment variables
_DANGEROUS_ENV_VARS = {
    "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS", "GCLOUD_SERVICE_KEY",
    "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID",
    "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN",
    "DATABASE_URL", "DB_PASSWORD",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "SECRET_KEY", "PRIVATE_KEY",
}

# Patterns for AWS/GCP commands
_AWS_COMMAND_PATTERNS = [
    r"\baws\s+", r"\bawscli\b", r"\bs3\s+", r"\bec2\s+",
    r"\blambda\s+invoke\b", r"\biam\s+",
]

_GCP_COMMAND_PATTERNS = [
    r"\bgcloud\s+", r"\bgsutil\s+", r"\bgcr\.io\b",
    r"\bgke\s+", r"\bcloud-sql\b",
]


def hasHooks(config: dict[str, Any]) -> bool:
    """Check if the configuration has any hooks defined.

    Args:
        config: Configuration dict with optional 'hooks' key.

    Returns:
        True if hooks are present.
    """
    hooks = config.get("hooks", {})
    if not hooks:
        return False
    for hook_type in ("pre_tool_use", "post_tool_use", "stop"):
        if hooks.get(hook_type):
            return True
    return False


def getHooksSources(config: dict[str, Any]) -> list[str]:
    """Get the source files where hooks are defined.

    Args:
        config: Configuration dict.

    Returns:
        List of source file paths.
    """
    sources = []
    for path in ["~/.jarvis/hooks.yaml", ".jarvis/hooks.yaml"]:
        expanded = os.path.expanduser(path)
        if os.path.isfile(expanded):
            sources.append(path)
    return sources


def hasBashPermission(rules: list[dict[str, Any]]) -> bool:
    """Check if any rule grants bash permission.

    Args:
        rules: List of permission rule dicts.

    Returns:
        True if bash is explicitly allowed.
    """
    return any(
        r.get("tool") == "bash" and r.get("behavior") == "allow"
        for r in rules
    )


def getBashPermissionSources(rules: list[dict[str, Any]]) -> list[str]:
    """Get sources of bash permission rules.

    Args:
        rules: List of permission rule dicts.

    Returns:
        List of source identifiers.
    """
    return [
        r.get("source", "unknown")
        for r in rules
        if r.get("tool") == "bash" and r.get("behavior") == "allow"
    ]


def formatListWithAnd(items: list[str]) -> str:
    """Format a list of strings with commas and 'and'.

    Args:
        items: List of strings.

    Returns:
        Formatted string like 'a, b, and c'.
    """
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def hasOtelHeadersHelper(env: dict[str, str] | None = None) -> bool:
    """Check if OpenTelemetry headers are configured.

    Args:
        env: Environment variables dict. Uses os.environ if None.

    Returns:
        True if OTEL headers are present.
    """
    env = env if env is not None else dict(os.environ)
    return bool(env.get("OTEL_EXPORTER_OTLP_HEADERS"))


def getOtelHeadersHelperSources(env: dict[str, str] | None = None) -> list[str]:
    """Get the source of OTEL header configuration.

    Args:
        env: Environment variables dict.

    Returns:
        List of sources where OTEL headers are configured.
    """
    sources = []
    env = env if env is not None else dict(os.environ)
    if env.get("OTEL_EXPORTER_OTLP_HEADERS"):
        sources.append("environment variable OTEL_EXPORTER_OTLP_HEADERS")
    return sources


def hasApiKeyHelper(env: dict[str, str] | None = None) -> bool:
    """Check if any API keys are present in the environment.

    Args:
        env: Environment variables dict.

    Returns:
        True if API keys are detected.
    """
    env = env if env is not None else dict(os.environ)
    key_patterns = ["_API_KEY", "_SECRET_KEY", "_TOKEN", "_PASSWORD"]
    return any(
        any(pat in k.upper() for pat in key_patterns)
        for k in env
    )


def getApiKeyHelperSources(env: dict[str, str] | None = None) -> list[str]:
    """Get environment variables that look like API keys.

    Args:
        env: Environment variables dict.

    Returns:
        List of variable names (not values) that look like API keys.
    """
    env = env if env is not None else dict(os.environ)
    key_patterns = ["_API_KEY", "_SECRET_KEY", "_TOKEN", "_PASSWORD"]
    return [
        k for k in env
        if any(pat in k.upper() for pat in key_patterns)
    ]


def hasAwsCommands(command: str) -> bool:
    """Check if a command contains AWS CLI commands.

    Args:
        command: Shell command string.

    Returns:
        True if AWS commands are detected.
    """
    return any(re.search(pat, command) for pat in _AWS_COMMAND_PATTERNS)


def getAwsCommandsSources(command: str) -> list[str]:
    """Get the AWS commands found in a command string.

    Args:
        command: Shell command string.

    Returns:
        List of matched AWS command patterns.
    """
    return [
        pat.replace(r"\b", "").replace(r"\s+", " ").rstrip("\\")
        for pat in _AWS_COMMAND_PATTERNS
        if re.search(pat, command)
    ]


def hasGcpCommands(command: str) -> bool:
    """Check if a command contains GCP CLI commands.

    Args:
        command: Shell command string.

    Returns:
        True if GCP commands are detected.
    """
    return any(re.search(pat, command) for pat in _GCP_COMMAND_PATTERNS)


def getGcpCommandsSources(command: str) -> list[str]:
    """Get the GCP commands found in a command string.

    Args:
        command: Shell command string.

    Returns:
        List of matched GCP command patterns.
    """
    return [
        pat.replace(r"\b", "").replace(r"\s+", " ").rstrip("\\")
        for pat in _GCP_COMMAND_PATTERNS
        if re.search(pat, command)
    ]


def hasDangerousEnvVars(env: dict[str, str] | None = None) -> bool:
    """Check if dangerous environment variables are set.

    Args:
        env: Environment variables dict.

    Returns:
        True if dangerous env vars are detected.
    """
    env = env if env is not None else dict(os.environ)
    return bool(_DANGEROUS_ENV_VARS.intersection(set(env.keys())))


def getDangerousEnvVarsSources(env: dict[str, str] | None = None) -> list[str]:
    """Get the names of dangerous environment variables that are set.

    Args:
        env: Environment variables dict.

    Returns:
        List of dangerous variable names.
    """
    env = env if env is not None else dict(os.environ)
    return sorted(_DANGEROUS_ENV_VARS.intersection(set(env.keys())))


def formatTrustLevel(level: str) -> str:
    """Format a trust level for terminal display.

    Args:
        level: Trust level string (trusted, untrusted, unknown).

    Returns:
        ANSI-colored trust level.
    """
    colors = {
        "trusted": GREEN,
        "untrusted": RED,
        "unknown": YELLOW,
        "sandboxed": CYAN,
    }
    color = colors.get(level, DIM)
    return f"{color}{BOLD}{level.upper()}{RESET}"


def formatSecurityWarning(
    warning_type: str,
    details: str = "",
) -> str:
    """Format a security warning for terminal display.

    Args:
        warning_type: Type of warning (api_key, aws, gcp, env_var, hook).
        details: Additional detail text.

    Returns:
        Formatted warning string.
    """
    warning_labels = {
        "api_key": "API keys detected in environment",
        "aws": "AWS credentials/commands detected",
        "gcp": "GCP credentials/commands detected",
        "env_var": "Sensitive environment variables detected",
        "hook": "Hooks may modify tool behavior",
        "bash": "Unrestricted shell access",
    }
    label = warning_labels.get(warning_type, warning_type)

    line = f"  {YELLOW}!{RESET} {BOLD}{label}{RESET}"
    if details:
        line += f"\n    {DIM}{details}{RESET}"
    return line


def formatSecuritySummary(
    command: str = "",
    env: dict[str, str] | None = None,
    rules: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Generate a full security summary for a tool call context.

    Args:
        command: Shell command being evaluated.
        env: Environment variables.
        rules: Permission rules.
        config: Configuration dict.

    Returns:
        Formatted security summary string.
    """
    warnings = []

    if command:
        if hasAwsCommands(command):
            sources = getAwsCommandsSources(command)
            warnings.append(formatSecurityWarning("aws", formatListWithAnd(sources)))
        if hasGcpCommands(command):
            sources = getGcpCommandsSources(command)
            warnings.append(formatSecurityWarning("gcp", formatListWithAnd(sources)))

    if hasDangerousEnvVars(env):
        vars_list = getDangerousEnvVarsSources(env)
        warnings.append(formatSecurityWarning("env_var", formatListWithAnd(vars_list)))

    if hasApiKeyHelper(env):
        keys = getApiKeyHelperSources(env)[:5]
        warnings.append(formatSecurityWarning("api_key", formatListWithAnd(keys)))

    if config and hasHooks(config):
        sources = getHooksSources(config)
        warnings.append(formatSecurityWarning("hook", formatListWithAnd(sources)))

    if not warnings:
        return f"  {GREEN}No security concerns detected.{RESET}"

    header = f"  {YELLOW}{BOLD}Security warnings ({len(warnings)}):{RESET}"
    return "\n".join([header] + warnings)
