"""
Per-sink analytics killswitch.

Uses dynamic config to disable individual analytics sinks at runtime.
"""

from typing import Literal

from .growthbook import get_dynamic_config_cached_may_be_stale

# Mangled name: per-sink analytics killswitch
SINK_KILLSWITCH_CONFIG_NAME = "tengu_frond_boric"

SinkName = Literal["datadog", "firstParty"]


def is_sink_killed(sink: SinkName) -> bool:
    """
    Check if a specific analytics sink is killed via GrowthBook config.

    Shape: { datadog?: bool, firstParty?: bool }
    A value of True for a key stops all dispatch to that sink.
    Default {} (nothing killed). Fail-open: missing/malformed config = sink stays on.
    """
    config: dict = get_dynamic_config_cached_may_be_stale(
        SINK_KILLSWITCH_CONFIG_NAME, {}
    )
    if config is None:
        return False
    return config.get(sink) is True
