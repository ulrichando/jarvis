"""Token and cost tracking for JARVIS LLM interactions."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from src.config import JARVIS_HOME

# Pricing per million tokens
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 3.75},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 0.75},
    "claude-haiku-4": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 0.25},
    # OpenAI
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    # Groq (free tier)
    "llama": {"input": 0.0, "output": 0.0},
    "qwen": {"input": 0.0, "output": 0.0},
    # DeepSeek
    "deepseek": {"input": 0.27, "output": 1.1},
}


@dataclass
class TokenUsage:
    """Accumulated token counts for a single model."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


class CostTracker:
    """Tracks token usage and estimated costs across a session."""

    def __init__(self, session_id: str = "") -> None:
        self._session_id = session_id or f"session-{int(time.time())}"
        self._model_usage: dict[str, TokenUsage] = {}
        self._total_cost: float = 0.0
        self._turn_count: int = 0
        self._start_time: float = time.time()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_usage(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> None:
        """Accumulate token usage for *model* and recalculate total cost."""
        if model not in self._model_usage:
            self._model_usage[model] = TokenUsage()

        usage = self._model_usage[model]
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.cache_read_tokens += cache_read
        usage.cache_write_tokens += cache_write
        self._turn_count += 1

        # Recalculate total cost from scratch to avoid floating-point drift.
        self._total_cost = sum(
            self._calculate_cost(m, u) for m, u in self._model_usage.items()
        )

    # ------------------------------------------------------------------
    # Cost calculation
    # ------------------------------------------------------------------

    def _calculate_cost(self, model: str, usage: TokenUsage) -> float:
        """Return estimated cost in USD for *usage* under *model*.

        Uses prefix matching: "claude-opus-4-6-20250514" matches "claude-opus-4".
        Longer prefixes are checked first so more-specific entries win.
        """
        pricing = self._resolve_pricing(model)
        if pricing is None:
            return 0.0

        cost = 0.0
        cost += usage.input_tokens * pricing.get("input", 0.0) / 1_000_000
        cost += usage.output_tokens * pricing.get("output", 0.0) / 1_000_000
        cost += usage.cache_read_tokens * pricing.get("cache_read", 0.0) / 1_000_000
        cost += usage.cache_write_tokens * pricing.get("cache_write", 0.0) / 1_000_000
        return cost

    @staticmethod
    def _resolve_pricing(model: str) -> dict[str, float] | None:
        """Find the best-matching pricing entry for *model* via prefix match."""
        model_lower = model.lower()
        best_key: str | None = None
        best_len = 0
        for key in MODEL_PRICING:
            if model_lower.startswith(key) and len(key) > best_len:
                best_key = key
                best_len = len(key)
        return MODEL_PRICING[best_key] if best_key else None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_session_cost(self) -> float:
        """Total estimated cost in USD for this session."""
        return self._total_cost

    def get_session_usage(self) -> dict[str, TokenUsage]:
        """Per-model accumulated token usage."""
        return dict(self._model_usage)

    def get_summary(self) -> str:
        """Human-readable multi-model summary.

        Example:
            Session: 45.2K tokens ($0.23) | opus: 30K in / 12K out | sonnet: 2K in / 1.2K out
        """
        total = sum(u.total_tokens for u in self._model_usage.values())
        parts = [f"Session: {self.format_tokens(total)} tokens ({self.format_cost(self._total_cost)})"]

        for model, usage in self._model_usage.items():
            # Use a short label derived from the model name.
            label = model.split("/")[-1]  # strip provider prefix if any
            for prefix in ("claude-", "gpt-"):
                if label.startswith(prefix):
                    label = label[len(prefix):]
                    break
            # Trim version suffixes like "-20250514"
            short = label.split("-")[0] if "-" in label else label
            parts.append(f"{short}: {self.format_tokens(usage.input_tokens)} in / {self.format_tokens(usage.output_tokens)} out")

        return " | ".join(parts)

    def get_status_line(self) -> str:
        """Short status suitable for a UI bar: ``$0.23 | 45.2K tokens``."""
        total = sum(u.total_tokens for u in self._model_usage.values())
        return f"{self.format_cost(self._total_cost)} | {self.format_tokens(total)} tokens"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | None = None) -> None:
        """Save session data to *path* or ``~/.jarvis/costs/{session_id}.json``."""
        dest = Path(path) if path else JARVIS_HOME / "costs" / f"{self._session_id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)

        lines_added, lines_removed = self.get_lines_changed()
        data = {
            "session_id": self._session_id,
            "start_time": self._start_time,
            "turn_count": self._turn_count,
            "total_cost": self._total_cost,
            "models": {m: asdict(u) for m, u in self._model_usage.items()},
            "lines_added": lines_added,
            "lines_removed": lines_removed,
        }
        dest.write_text(json.dumps(data, indent=2))

    def load(self, path: str | None = None) -> None:
        """Load session data from *path* or the default location."""
        src = Path(path) if path else JARVIS_HOME / "costs" / f"{self._session_id}.json"
        if not src.exists():
            return

        data = json.loads(src.read_text())
        self._session_id = data.get("session_id", self._session_id)
        self._start_time = data.get("start_time", self._start_time)
        self._turn_count = data.get("turn_count", 0)
        self._total_cost = data.get("total_cost", 0.0)
        self._model_usage = {}
        for model, usage_dict in data.get("models", {}).items():
            self._model_usage[model] = TokenUsage(**usage_dict)
        self._lines_added = data.get("lines_added", 0)
        self._lines_removed = data.get("lines_removed", 0)

    # ------------------------------------------------------------------
    # Lines changed tracking
    # ------------------------------------------------------------------

    def add_lines_changed(self, added: int = 0, removed: int = 0) -> None:
        if not hasattr(self, "_lines_added"):
            self._lines_added = 0
            self._lines_removed = 0
        self._lines_added += added
        self._lines_removed += removed

    def get_lines_changed(self) -> tuple[int, int]:
        return (getattr(self, "_lines_added", 0), getattr(self, "_lines_removed", 0))

    def get_total_duration(self) -> float:
        return time.time() - self._start_time

    def format_total_cost(self) -> str:
        lines_added, lines_removed = self.get_lines_changed()
        parts = [self.get_summary()]
        if lines_added or lines_removed:
            parts.append(f"{lines_added} lines added, {lines_removed} lines removed")
        return " | ".join(parts)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds >= 3600:
            return f"{seconds / 3600:.1f}h"
        if seconds >= 60:
            return f"{seconds / 60:.1f}m"
        return f"{seconds:.1f}s"

    def reset(self) -> None:
        """Clear all counters and start fresh."""
        self._model_usage.clear()
        self._total_cost = 0.0
        self._turn_count = 0
        self._start_time = time.time()
        self._lines_added = 0
        self._lines_removed = 0

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_tokens(n: int) -> str:
        """Format token count: ``1.2K``, ``45.2K``, ``1.2M``."""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    @staticmethod
    def format_cost(cost: float) -> str:
        """Format cost in USD: ``$0.00``, ``$1.23``, ``$12.34``."""
        return f"${cost:.2f}"


# ======================================================================
# Module-level singleton
# ======================================================================

_tracker: CostTracker | None = None


def get_tracker(session_id: str = "") -> CostTracker:
    """Return the module-level singleton, creating it if necessary."""
    global _tracker
    if _tracker is None:
        _tracker = CostTracker(session_id=session_id)
    return _tracker


def record_usage(model: str, **kwargs) -> None:
    """Convenience wrapper around the singleton's ``record_usage``."""
    get_tracker().record_usage(model, **kwargs)
