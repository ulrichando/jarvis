"""
GrowthBook feature flag and dynamic config client.

Provides cached feature flag evaluation and dynamic configuration
for analytics and feature gating.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar, Generic, Optional

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class GrowthBookUserAttributes:
    id: str = ""
    session_id: str = ""
    device_id: str = ""
    platform: str = ""
    api_base_url_host: str | None = None
    organization_uuid: str | None = None
    account_uuid: str | None = None
    user_type: str | None = None
    subscription_type: str | None = None
    rate_limit_tier: str | None = None
    first_token_time: float | None = None
    email: str | None = None
    app_version: str | None = None


# Module state
_client: Any = None
_client_created_with_auth: bool = False
_remote_eval_feature_values: dict[str, Any] = {}
_cached_feature_values: dict[str, Any] = {}
_refresh_listeners: list[Callable[[], None]] = []
_env_overrides: dict[str, Any] | None = None
_env_overrides_parsed: bool = False


def _get_env_overrides() -> dict[str, Any] | None:
    """Parse env var overrides for feature flags."""
    global _env_overrides, _env_overrides_parsed
    if not _env_overrides_parsed:
        _env_overrides_parsed = True
        if os.environ.get("USER_TYPE") == "ant":
            raw = os.environ.get("CLAUDE_INTERNAL_FC_OVERRIDES")
            if raw:
                try:
                    _env_overrides = json.loads(raw)
                except Exception:
                    logger.error(
                        f"GrowthBook: Failed to parse CLAUDE_INTERNAL_FC_OVERRIDES: {raw}"
                    )
    return _env_overrides


def has_growthbook_env_override(feature: str) -> bool:
    """Check if a feature has an env-var override."""
    overrides = _get_env_overrides()
    return overrides is not None and feature in overrides


def get_feature_value_cached_may_be_stale(
    feature_key: str, default_value: T
) -> T:
    """
    Get a feature flag value, using cache if available.

    Returns the cached value if available, otherwise the default.
    This may be stale if GrowthBook hasn't refreshed recently.
    """
    overrides = _get_env_overrides()
    if overrides is not None and feature_key in overrides:
        return overrides[feature_key]

    if feature_key in _remote_eval_feature_values:
        return _remote_eval_feature_values[feature_key]

    if feature_key in _cached_feature_values:
        return _cached_feature_values[feature_key]

    return default_value


def check_statsig_feature_gate_cached_may_be_stale(gate_name: str) -> bool:
    """Check a feature gate, using cache. Returns False if not found."""
    return get_feature_value_cached_may_be_stale(gate_name, False)


def get_dynamic_config_cached_may_be_stale(
    config_name: str, default_value: T
) -> T:
    """Get a dynamic config value, using cache."""
    return get_feature_value_cached_may_be_stale(config_name, default_value)


def on_growthbook_refresh(listener: Callable[[], None]) -> Callable[[], None]:
    """
    Register a callback to fire when GrowthBook feature values refresh.
    Returns an unsubscribe function.
    """
    _refresh_listeners.append(listener)

    def unsubscribe() -> None:
        if listener in _refresh_listeners:
            _refresh_listeners.remove(listener)

    return unsubscribe


def _notify_refresh() -> None:
    """Notify all refresh listeners."""
    for listener in _refresh_listeners:
        try:
            listener()
        except Exception as e:
            logger.error(f"GrowthBook refresh listener error: {e}")


async def initialize_growthbook() -> None:
    """Initialize the GrowthBook client."""
    # Stub -- full implementation would connect to GrowthBook API
    pass


def reset_growthbook() -> None:
    """Reset GrowthBook state for testing or re-initialization."""
    global _client, _client_created_with_auth
    _client = None
    _client_created_with_auth = False
    _remote_eval_feature_values.clear()
    _cached_feature_values.clear()


@dataclass
class GrowthBookExperimentData:
    experiment_id: str
    variation_id: int
    user_attributes: GrowthBookUserAttributes | None = None
    experiment_metadata: dict[str, Any] | None = None
