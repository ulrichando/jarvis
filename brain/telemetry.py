"""
Token telemetry — records every API call with token counts and cost estimates.
Import the global `telemetry` singleton and call telemetry.record() after every
API call to build a live breakdown of where tokens are going.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("jarvis.tokens")


@dataclass
class APICall:
    channel_id:    str
    route:         Literal["inline", "qwen", "claude"]
    tool_name:     str | None
    input_tokens:  int
    output_tokens: int
    latency_ms:    float
    message:       str          # first 80 chars only for debugging
    timestamp:     float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        # Claude Haiku 4.5 pricing
        input_cost  = (self.input_tokens  / 1_000_000) * 0.80
        output_cost = (self.output_tokens / 1_000_000) * 4.00
        return round(input_cost + output_cost, 6)


class TokenTelemetry:
    """
    Tracks every API call with token counts, cost, and channel breakdown.
    Call report() to see a full breakdown of where tokens are going.
    """

    def __init__(self) -> None:
        self._calls: list[APICall] = []

    def record(self, call: APICall) -> None:
        self._calls.append(call)
        logger.warning(
            f"[TOKEN] channel={call.channel_id} route={call.route} "
            f"tool={call.tool_name or 'none'} "
            f"in={call.input_tokens} out={call.output_tokens} "
            f"total={call.total_tokens} cost=${call.estimated_cost_usd:.6f} "
            f"latency={call.latency_ms:.0f}ms "
            f"msg='{call.message[:80]}'"
        )

    def report(self) -> dict:
        """Return full breakdown — call this to diagnose leaks."""
        by_channel: dict = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
        by_route:   dict = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
        by_tool:    dict = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})

        for c in self._calls:
            for bucket, key in [
                (by_channel, c.channel_id),
                (by_route,   c.route),
                (by_tool,    c.tool_name or "no_tool"),
            ]:
                bucket[key]["calls"]  += 1
                bucket[key]["tokens"] += c.total_tokens
                bucket[key]["cost"]   += c.estimated_cost_usd

        total_tokens = sum(c.total_tokens for c in self._calls)
        total_cost   = sum(c.estimated_cost_usd for c in self._calls)

        return {
            "total_calls":  len(self._calls),
            "total_tokens": total_tokens,
            "total_cost":   f"${total_cost:.4f}",
            "by_channel":   dict(by_channel),
            "by_route":     dict(by_route),
            "by_tool":      dict(by_tool),
        }

    def top_offenders(self, n: int = 10) -> list[APICall]:
        """Return the N most expensive individual calls."""
        return sorted(self._calls, key=lambda c: c.total_tokens, reverse=True)[:n]

    def clear(self) -> None:
        self._calls.clear()


# Global singleton — import this everywhere
telemetry = TokenTelemetry()
