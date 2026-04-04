"""Voice feature enablement check."""

from __future__ import annotations

from typing import Callable, Optional


def is_voice_enabled(
    user_intent: bool = False,
    has_voice_auth: Callable[[], bool] = lambda: False,
    is_growthbook_enabled: Callable[[], bool] = lambda: True,
) -> bool:
    """Check if voice mode is enabled.

    Combines user intent (settings.voiceEnabled) with auth and feature flag.

    Equivalent to useVoiceEnabled React hook.
    """
    return user_intent and has_voice_auth() and is_growthbook_enabled()
