"""
Cost summary hook -- displays cost information on exit.

Converted from costHook.ts -- originally a React useEffect hook,
now a simple atexit handler registration.
"""

from __future__ import annotations

import atexit
from typing import Callable, Optional

from .cost_tracker import format_total_cost, save_current_session_costs


class FpsMetrics:
    """Placeholder for FPS metrics (terminal rendering performance)."""
    average_fps: Optional[float] = None
    low_1_pct_fps: Optional[float] = None


def use_cost_summary(
    get_fps_metrics: Optional[Callable[[], Optional[FpsMetrics]]] = None,
) -> None:
    """
    Register an atexit handler that prints cost summary and saves session costs.

    In the original TypeScript, this was a React useEffect hook. In Python,
    we register an atexit handler instead.
    """

    def _on_exit() -> None:
        # In a full implementation, would check has_console_billing_access()
        print()
        print(format_total_cost())

        fps_metrics = get_fps_metrics() if get_fps_metrics else None
        save_current_session_costs(fps_metrics)

    atexit.register(_on_exit)
