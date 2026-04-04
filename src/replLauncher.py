"""REPL launcher.

In the TypeScript version, this launches the React-based REPL UI
(App + REPL components). The Python version provides the launch
logic without React/JSX.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class FpsMetrics:
    average: float = 0.0
    low_1_pct: float = 0.0


@dataclass
class AppWrapperProps:
    get_fps_metrics: Optional[Callable[[], Optional[FpsMetrics]]] = None
    stats: Optional[Any] = None
    initial_state: Optional[Any] = None


async def launch_repl(
    app_props: AppWrapperProps,
    repl_props: Optional[Dict[str, Any]] = None,
    render_and_run: Optional[Callable] = None,
) -> None:
    """Launch the REPL.

    In the TypeScript version, this renders the App and REPL React
    components into an Ink root. The Python version would start
    the interactive CLI loop via JARVIS's shell system.
    """
    # In full implementation, this would:
    # 1. Initialize the REPL state
    # 2. Start the interactive input loop
    # 3. Process commands and queries
    # 4. Handle tool calls and responses
    pass
