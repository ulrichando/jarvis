"""Model cost tracking and USD calculation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelCosts:
    """Per-million-token pricing for a model."""
    input_tokens: float
    output_tokens: float
    prompt_cache_write_tokens: float
    prompt_cache_read_tokens: float
    web_search_requests: float = 0.01


# Standard pricing tiers
COST_TIER_3_15 = ModelCosts(
    input_tokens=3,
    output_tokens=15,
    prompt_cache_write_tokens=3.75,
    prompt_cache_read_tokens=0.3,
)

COST_TIER_15_75 = ModelCosts(
    input_tokens=15,
    output_tokens=75,
    prompt_cache_write_tokens=18.75,
    prompt_cache_read_tokens=1.5,
)

COST_TIER_5_25 = ModelCosts(
    input_tokens=5,
    output_tokens=25,
    prompt_cache_write_tokens=6.25,
    prompt_cache_read_tokens=0.5,
)

COST_TIER_30_150 = ModelCosts(
    input_tokens=30,
    output_tokens=150,
    prompt_cache_write_tokens=37.5,
    prompt_cache_read_tokens=3,
)

COST_HAIKU_35 = ModelCosts(
    input_tokens=0.8,
    output_tokens=4,
    prompt_cache_write_tokens=1,
    prompt_cache_read_tokens=0.08,
)

COST_HAIKU_45 = ModelCosts(
    input_tokens=1,
    output_tokens=5,
    prompt_cache_write_tokens=1.25,
    prompt_cache_read_tokens=0.1,
)

DEFAULT_UNKNOWN_MODEL_COST = COST_TIER_5_25

# Model name to cost mapping
MODEL_COSTS: dict[str, ModelCosts] = {
    "claude-3-5-haiku": COST_HAIKU_35,
    "claude-haiku-4-5": COST_HAIKU_45,
    "claude-3-5-sonnet-v2": COST_TIER_3_15,
    "claude-3-7-sonnet": COST_TIER_3_15,
    "claude-sonnet-4": COST_TIER_3_15,
    "claude-sonnet-4-5": COST_TIER_3_15,
    "claude-sonnet-4-6": COST_TIER_3_15,
    "claude-opus-4": COST_TIER_15_75,
    "claude-opus-4-1": COST_TIER_15_75,
    "claude-opus-4-5": COST_TIER_5_25,
    "claude-opus-4-6": COST_TIER_5_25,
}


@dataclass
class Usage:
    """Token usage from an API response."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    speed: Optional[str] = None


def _tokens_to_usd_cost(model_costs: ModelCosts, usage: Usage) -> float:
    """Calculate USD cost based on token usage and model cost config."""
    return (
        (usage.input_tokens / 1_000_000) * model_costs.input_tokens
        + (usage.output_tokens / 1_000_000) * model_costs.output_tokens
        + (usage.cache_read_input_tokens / 1_000_000) * model_costs.prompt_cache_read_tokens
        + (usage.cache_creation_input_tokens / 1_000_000) * model_costs.prompt_cache_write_tokens
        + usage.web_search_requests * model_costs.web_search_requests
    )


def get_model_costs(model: str, usage: Optional[Usage] = None) -> ModelCosts:
    """Get cost configuration for a model."""
    # Normalize model name
    model_lower = model.lower()
    for name, costs in MODEL_COSTS.items():
        if name in model_lower:
            # Special handling for Opus 4.6 fast mode
            if name == "claude-opus-4-6" and usage and usage.speed == "fast":
                return COST_TIER_30_150
            return costs
    return DEFAULT_UNKNOWN_MODEL_COST


def calculate_usd_cost(resolved_model: str, usage: Usage) -> float:
    """Calculate the cost of a query in US dollars."""
    model_costs = get_model_costs(resolved_model, usage)
    return _tokens_to_usd_cost(model_costs, usage)


def calculate_cost_from_tokens(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float:
    """Calculate cost from raw token counts."""
    usage = Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
    )
    return calculate_usd_cost(model, usage)


def _format_price(price: float) -> str:
    if price == int(price):
        return f"${int(price)}"
    return f"${price:.2f}"


def format_model_pricing(costs: ModelCosts) -> str:
    """Format model costs as a pricing string for display."""
    return f"{_format_price(costs.input_tokens)}/{_format_price(costs.output_tokens)} per Mtok"


def get_model_pricing_string(model: str) -> Optional[str]:
    """Get formatted pricing string for a model."""
    costs = get_model_costs(model)
    if costs is DEFAULT_UNKNOWN_MODEL_COST:
        return None
    return format_model_pricing(costs)
