"""
Cost tracking for API usage across a session.

Converted from cost-tracker.ts -- tracks token usage, costs, and
session metrics for display and persistence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# --- Data Types ---

@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    cost_usd: float = 0.0
    context_window: int = 0
    max_output_tokens: int = 0


@dataclass
class FpsMetrics:
    average_fps: Optional[float] = None
    low_1_pct_fps: Optional[float] = None


@dataclass
class Usage:
    """Mirrors Anthropic API usage response."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    server_tool_use: Optional[dict[str, Any]] = None
    speed: Optional[str] = None


@dataclass
class StoredCostState:
    total_cost_usd: float = 0.0
    total_api_duration: float = 0.0
    total_api_duration_without_retries: float = 0.0
    total_tool_duration: float = 0.0
    total_lines_added: int = 0
    total_lines_removed: int = 0
    last_duration: Optional[float] = None
    model_usage: Optional[dict[str, ModelUsage]] = None


# --- Global State ---

_state = {
    "total_cost_usd": 0.0,
    "total_api_duration": 0.0,
    "total_api_duration_without_retries": 0.0,
    "total_tool_duration": 0.0,
    "total_duration": 0.0,
    "total_lines_added": 0,
    "total_lines_removed": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cache_creation_input_tokens": 0,
    "total_cache_read_input_tokens": 0,
    "total_web_search_requests": 0,
    "has_unknown_model_cost": False,
    "model_usage": {},  # dict[str, ModelUsage]
    "session_id": "",
}


# --- Getters ---

def get_total_cost_usd() -> float:
    return _state["total_cost_usd"]


# Alias
get_total_cost = get_total_cost_usd


def get_total_duration() -> float:
    return _state["total_duration"]


def get_total_api_duration() -> float:
    return _state["total_api_duration"]


def get_total_api_duration_without_retries() -> float:
    return _state["total_api_duration_without_retries"]


def get_total_tool_duration() -> float:
    return _state["total_tool_duration"]


def get_total_lines_added() -> int:
    return _state["total_lines_added"]


def get_total_lines_removed() -> int:
    return _state["total_lines_removed"]


def get_total_input_tokens() -> int:
    return _state["total_input_tokens"]


def get_total_output_tokens() -> int:
    return _state["total_output_tokens"]


def get_total_cache_read_input_tokens() -> int:
    return _state["total_cache_read_input_tokens"]


def get_total_cache_creation_input_tokens() -> int:
    return _state["total_cache_creation_input_tokens"]


def get_total_web_search_requests() -> int:
    return _state["total_web_search_requests"]


def has_unknown_model_cost() -> bool:
    return _state["has_unknown_model_cost"]


def set_has_unknown_model_cost(value: bool) -> None:
    _state["has_unknown_model_cost"] = value


def get_model_usage() -> dict[str, ModelUsage]:
    return _state["model_usage"]


def get_usage_for_model(model: str) -> Optional[ModelUsage]:
    return _state["model_usage"].get(model)


# --- Mutations ---

def add_to_total_lines_changed(added: int, removed: int) -> None:
    _state["total_lines_added"] += added
    _state["total_lines_removed"] += removed


def add_to_total_cost_state(cost: float, model_usage: ModelUsage, model: str) -> None:
    _state["total_cost_usd"] += cost
    _state["model_usage"][model] = model_usage


def reset_cost_state() -> None:
    """Reset all cost tracking state."""
    _state.update({
        "total_cost_usd": 0.0,
        "total_api_duration": 0.0,
        "total_api_duration_without_retries": 0.0,
        "total_tool_duration": 0.0,
        "total_duration": 0.0,
        "total_lines_added": 0,
        "total_lines_removed": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_creation_input_tokens": 0,
        "total_cache_read_input_tokens": 0,
        "total_web_search_requests": 0,
        "has_unknown_model_cost": False,
        "model_usage": {},
    })


def reset_state_for_tests() -> None:
    reset_cost_state()


def set_cost_state_for_restore(data: StoredCostState) -> None:
    """Restore cost state from stored session data."""
    _state["total_cost_usd"] = data.total_cost_usd
    _state["total_api_duration"] = data.total_api_duration
    _state["total_api_duration_without_retries"] = data.total_api_duration_without_retries
    _state["total_tool_duration"] = data.total_tool_duration
    _state["total_lines_added"] = data.total_lines_added
    _state["total_lines_removed"] = data.total_lines_removed
    if data.model_usage:
        _state["model_usage"] = dict(data.model_usage)


# --- Session Persistence ---

def get_stored_session_costs(session_id: str) -> Optional[StoredCostState]:
    """Get stored cost state for a specific session. Returns None if session mismatch."""
    # In a full implementation, would read from project config
    return None


def restore_cost_state_for_session(session_id: str) -> bool:
    """Restore cost state when resuming a session. Returns True if restored."""
    data = get_stored_session_costs(session_id)
    if data is None:
        return False
    set_cost_state_for_restore(data)
    return True


def save_current_session_costs(fps_metrics: Optional[FpsMetrics] = None) -> None:
    """Save the current session's costs to project config."""
    # In a full implementation, would save to project config file
    pass


# --- Formatting ---

def format_cost(cost: float, max_decimal_places: int = 4) -> str:
    """Format a cost value as a dollar amount."""
    if cost > 0.5:
        rounded = math.floor(cost * 100 + 0.5) / 100
        return f"${rounded:.2f}"
    return f"${cost:.{max_decimal_places}f}"


def format_number(n: int) -> str:
    """Format a number with comma separators."""
    return f"{n:,}"


def format_duration(ms: float) -> str:
    """Format a duration in milliseconds to human-readable form."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remaining = seconds % 60
    return f"{minutes}m {remaining:.1f}s"


def _get_canonical_name(model: str) -> str:
    """Get a short display name for a model."""
    # Simplified -- the real implementation has full model name mapping
    parts = model.split("/")
    return parts[-1] if parts else model


def _format_model_usage() -> str:
    """Format per-model usage breakdown."""
    model_usage_map = get_model_usage()
    if not model_usage_map:
        return "Usage:                 0 input, 0 output, 0 cache read, 0 cache write"

    # Accumulate by short name
    usage_by_short: dict[str, ModelUsage] = {}
    for model, usage in model_usage_map.items():
        short_name = _get_canonical_name(model)
        if short_name not in usage_by_short:
            usage_by_short[short_name] = ModelUsage()
        acc = usage_by_short[short_name]
        acc.input_tokens += usage.input_tokens
        acc.output_tokens += usage.output_tokens
        acc.cache_read_input_tokens += usage.cache_read_input_tokens
        acc.cache_creation_input_tokens += usage.cache_creation_input_tokens
        acc.web_search_requests += usage.web_search_requests
        acc.cost_usd += usage.cost_usd

    result = "Usage by model:"
    for short_name, usage in usage_by_short.items():
        parts = [
            f"  {format_number(usage.input_tokens)} input",
            f"{format_number(usage.output_tokens)} output",
            f"{format_number(usage.cache_read_input_tokens)} cache read",
            f"{format_number(usage.cache_creation_input_tokens)} cache write",
        ]
        if usage.web_search_requests > 0:
            parts.append(f"{format_number(usage.web_search_requests)} web search")
        usage_string = ", ".join(parts) + f" ({format_cost(usage.cost_usd)})"
        result += f"\n{(short_name + ':').rjust(21)}{usage_string}"
    return result


def format_total_cost() -> str:
    """Format the complete cost summary for display."""
    cost_display = format_cost(get_total_cost_usd())
    if has_unknown_model_cost():
        cost_display += " (costs may be inaccurate due to usage of unknown models)"

    model_usage_display = _format_model_usage()
    lines_added = get_total_lines_added()
    lines_removed = get_total_lines_removed()

    return (
        f"Total cost:            {cost_display}\n"
        f"Total duration (API):  {format_duration(get_total_api_duration())}\n"
        f"Total duration (wall): {format_duration(get_total_duration())}\n"
        f"Total code changes:    {lines_added} {'line' if lines_added == 1 else 'lines'} added, "
        f"{lines_removed} {'line' if lines_removed == 1 else 'lines'} removed\n"
        f"{model_usage_display}"
    )


# --- Cost Calculation ---

def _add_to_total_model_usage(cost: float, usage: Usage, model: str) -> ModelUsage:
    """Add usage to the per-model accumulator."""
    model_usage = get_usage_for_model(model) or ModelUsage()

    model_usage.input_tokens += usage.input_tokens
    model_usage.output_tokens += usage.output_tokens
    model_usage.cache_read_input_tokens += usage.cache_read_input_tokens or 0
    model_usage.cache_creation_input_tokens += usage.cache_creation_input_tokens or 0
    if usage.server_tool_use:
        model_usage.web_search_requests += usage.server_tool_use.get("web_search_requests", 0)
    model_usage.cost_usd += cost
    return model_usage


def add_to_total_session_cost(cost: float, usage: Usage, model: str) -> float:
    """Add a cost entry to the running session total. Returns total cost added."""
    model_usage = _add_to_total_model_usage(cost, usage, model)
    add_to_total_cost_state(cost, model_usage, model)
    return cost
