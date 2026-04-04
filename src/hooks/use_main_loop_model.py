"""Main loop model resolution."""

from __future__ import annotations

from typing import Callable, Optional


def get_main_loop_model(
    main_loop_model: Optional[str] = None,
    main_loop_model_for_session: Optional[str] = None,
    default_model: str = "claude-sonnet-4-20250514",
    parse_model: Optional[Callable[[str], str]] = None,
) -> str:
    """Resolve the main loop model setting.

    Equivalent to useMainLoopModel React hook.
    """
    raw = main_loop_model_for_session or main_loop_model or default_model
    if parse_model:
        return parse_model(raw)
    return raw
