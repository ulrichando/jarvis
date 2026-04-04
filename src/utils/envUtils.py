"""
Environment variable utilities.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union


@lru_cache(maxsize=1)
def get_config_home_dir() -> str:
    """Get the JARVIS configuration home directory."""
    config_dir = os.environ.get("JARVIS_CONFIG_DIR")
    if config_dir:
        return os.path.normpath(config_dir)
    return str(Path.home() / ".jarvis")


def get_teams_dir() -> str:
    """Get the teams directory path."""
    return os.path.join(get_config_home_dir(), "teams")


def is_env_truthy(env_var: Union[str, bool, None]) -> bool:
    """Check if an environment variable value is truthy."""
    if not env_var:
        return False
    if isinstance(env_var, bool):
        return env_var
    return env_var.lower().strip() in ("1", "true", "yes", "on")


def is_env_defined_falsy(env_var: Union[str, bool, None]) -> bool:
    """Check if an environment variable is explicitly set to a falsy value."""
    if env_var is None:
        return False
    if isinstance(env_var, bool):
        return not env_var
    if not env_var:
        return False
    return env_var.lower().strip() in ("0", "false", "no", "off")


def is_bare_mode() -> bool:
    """Check if running in bare mode (skip hooks, LSP, plugins, etc.)."""
    return is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE")) or "--bare" in sys.argv


def parse_env_vars(raw_env_args: Optional[list[str]]) -> dict[str, str]:
    """
    Parse an array of environment variable strings into a key-value dict.

    Args:
        raw_env_args: List of strings in KEY=VALUE format.

    Returns:
        Dict with key-value pairs.

    Raises:
        ValueError: If a string is not in KEY=VALUE format.
    """
    parsed: dict[str, str] = {}
    if raw_env_args:
        for env_str in raw_env_args:
            parts = env_str.split("=", 1)
            if len(parts) != 2 or not parts[0]:
                raise ValueError(
                    f"Invalid environment variable format: {env_str}, "
                    "environment variables should be added as: -e KEY1=value1 -e KEY2=value2"
                )
            parsed[parts[0]] = parts[1]
    return parsed


def get_aws_region() -> str:
    """Get the AWS region with fallback to default."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def get_default_vertex_region() -> str:
    """Get the default Vertex AI region."""
    return os.environ.get("CLOUD_ML_REGION") or "us-east5"


def should_maintain_project_working_dir() -> bool:
    """Check if bash commands should maintain project working directory."""
    return is_env_truthy(os.environ.get("CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"))


def is_running_on_homespace() -> bool:
    """Check if running on Homespace (ant-internal cloud environment)."""
    return (
        os.environ.get("USER_TYPE") == "ant"
        and is_env_truthy(os.environ.get("COO_RUNNING_ON_HOMESPACE"))
    )


# Model prefix -> env var for Vertex region overrides
VERTEX_REGION_OVERRIDES: list[tuple[str, str]] = [
    ("claude-haiku-4-5", "VERTEX_REGION_CLAUDE_HAIKU_4_5"),
    ("claude-3-5-haiku", "VERTEX_REGION_CLAUDE_3_5_HAIKU"),
    ("claude-3-5-sonnet", "VERTEX_REGION_CLAUDE_3_5_SONNET"),
    ("claude-3-7-sonnet", "VERTEX_REGION_CLAUDE_3_7_SONNET"),
    ("claude-opus-4-1", "VERTEX_REGION_CLAUDE_4_1_OPUS"),
    ("claude-opus-4", "VERTEX_REGION_CLAUDE_4_0_OPUS"),
    ("claude-sonnet-4-6", "VERTEX_REGION_CLAUDE_4_6_SONNET"),
    ("claude-sonnet-4-5", "VERTEX_REGION_CLAUDE_4_5_SONNET"),
    ("claude-sonnet-4", "VERTEX_REGION_CLAUDE_4_0_SONNET"),
]


def get_vertex_region_for_model(model: Optional[str] = None) -> str:
    """Get the Vertex AI region for a specific model."""
    if model:
        for prefix, env_var in VERTEX_REGION_OVERRIDES:
            if model.startswith(prefix):
                return os.environ.get(env_var) or get_default_vertex_region()
    return get_default_vertex_region()
