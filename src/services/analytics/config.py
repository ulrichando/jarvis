"""
Shared analytics configuration.

Common logic for determining when analytics should be disabled
across all analytics systems.
"""

import os


def is_analytics_disabled() -> bool:
    """
    Check if analytics operations should be disabled.

    Analytics is disabled in the following cases:
    - Test environment (NODE_ENV == 'test')
    - Third-party cloud providers (Bedrock/Vertex)
    - Privacy level is no-telemetry or essential-traffic
    """
    return (
        os.environ.get("NODE_ENV") == "test"
        or _is_env_truthy(os.environ.get("CLAUDE_CODE_USE_BEDROCK"))
        or _is_env_truthy(os.environ.get("CLAUDE_CODE_USE_VERTEX"))
        or _is_env_truthy(os.environ.get("CLAUDE_CODE_USE_FOUNDRY"))
        or is_telemetry_disabled()
    )


def is_feedback_survey_disabled() -> bool:
    """
    Check if the feedback survey should be suppressed.

    Unlike is_analytics_disabled(), this does NOT block on 3P providers
    (Bedrock/Vertex/Foundry). The survey is a local UI prompt with no
    transcript data.
    """
    return os.environ.get("NODE_ENV") == "test" or is_telemetry_disabled()


def _is_env_truthy(val: str | None) -> bool:
    return val is not None and val.lower() in ("1", "true", "yes")


def is_telemetry_disabled() -> bool:
    """Check if telemetry is globally disabled via privacy level."""
    # Stub -- in full system this would check privacy config
    return False
