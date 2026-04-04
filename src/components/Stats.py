"""Usage statistics display for ANSI terminals.

Renders:
- Token usage per model (input/output/cache)
- Cost breakdown
- Session duration
- Lines changed (added/removed)
- Tool call count by type
- Nice table with ANSI colors
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

# ANSI codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"
WHITE = "\033[97m"

# Layout constants
COL1_LABEL_WIDTH = 20
COL2_START = 42
COL2_LABEL_WIDTH = 16

DATE_RANGE_LABELS = {
    "today": "Today",
    "week": "This Week",
    "month": "This Month",
    "all": "All Time",
}


def _fmt_tokens(n: int) -> str:
    """Format token count compactly."""
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}K"
    if n < 1000000:
        return f"{n // 1000}K"
    return f"{n / 1000000:.1f}M"


def _fmt_cost(cost: float) -> str:
    """Format cost."""
    if cost < 0.001:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    if cost < 1.0:
        return f"${cost:.3f}"
    return f"${cost:.2f}"


def _fmt_duration(seconds: float) -> str:
    """Format duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _bar(value: float, max_value: float, width: int = 20, color: str = CYAN) -> str:
    """Render a horizontal bar chart."""
    if max_value <= 0:
        return ""
    filled = int((value / max_value) * width)
    filled = min(filled, width)
    return f"{color}{'█' * filled}{DIM}{'░' * (width - filled)}{RESET}"


def _row(label: str, value: str, label_width: int = COL1_LABEL_WIDTH) -> str:
    """Render a label: value row."""
    padded_label = label.ljust(label_width)
    return f"  {DIM}{padded_label}{RESET} {value}"


def _divider(width: int = 50) -> str:
    """Render a section divider."""
    return f"  {DIM}{'─' * width}{RESET}"


@dataclass
class ModelUsage:
    """Token usage for a single model."""
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_write


@dataclass
class StatsResult:
    """Aggregated statistics for a session or time range."""
    models: list[ModelUsage] = field(default_factory=list)
    total_input: int = 0
    total_output: int = 0
    total_cache_read: int = 0
    total_cache_write: int = 0
    total_cost: float = 0.0
    total_calls: int = 0
    duration_seconds: float = 0.0
    lines_added: int = 0
    lines_removed: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    date_range: str = "today"
    session_count: int = 1

    @property
    def total_tokens(self) -> int:
        return self.total_input + self.total_output + self.total_cache_read + self.total_cache_write


@dataclass
class ChartOutput:
    """Output from chart generation."""
    lines: list[str] = field(default_factory=list)
    legend: list[str] = field(default_factory=list)


@dataclass
class ChartLegend:
    """Legend entry for a chart."""
    label: str
    color: str
    value: str


class StatsContentProps:
    def __init__(self, stats: Optional[StatsResult] = None):
        self.stats = stats


class ModelEntryProps:
    def __init__(self, usage: Optional[ModelUsage] = None):
        self.usage = usage


class Props:
    def __init__(self, date_range: str = "today"):
        self.date_range = date_range


def formatPeakDay(stats: StatsResult) -> str:
    """Format peak usage day info."""
    if not stats.models:
        return "No data"
    top = max(stats.models, key=lambda m: m.total_tokens)
    return f"{top.model}: {_fmt_tokens(top.total_tokens)} tokens"


def getNextDateRange(current: str) -> str:
    """Cycle to next date range."""
    order = ["today", "week", "month", "all"]
    idx = order.index(current) if current in order else 0
    return order[(idx + 1) % len(order)]


def createAllTimeStatsPromise(stats: StatsResult) -> StatsResult:
    """Placeholder for async stats loading (returns stats directly in Python)."""
    return stats


def DateRangeSelector(current: str = "today") -> str:
    """Render date range selector."""
    ranges = ["today", "week", "month", "all"]
    parts = []
    for r in ranges:
        label = DATE_RANGE_LABELS.get(r, r)
        if r == current:
            parts.append(f"{BOLD}{CYAN}{label}{RESET}")
        else:
            parts.append(f"{DIM}{label}{RESET}")
    return "  " + " | ".join(parts)


def generateTokenChart(stats: StatsResult, width: int = 40) -> ChartOutput:
    """Generate a horizontal bar chart of token usage by model."""
    if not stats.models:
        return ChartOutput()

    max_tokens = max(m.total_tokens for m in stats.models) if stats.models else 1
    model_colors = [CYAN, BLUE, MAGENTA, YELLOW, GREEN, RED]

    lines: list[str] = []
    legend: list[str] = []

    for i, model in enumerate(sorted(stats.models, key=lambda m: -m.total_tokens)):
        color = model_colors[i % len(model_colors)]
        bar = _bar(model.total_tokens, max_tokens, width=width, color=color)
        name = model.model[:18].ljust(18)
        lines.append(f"  {name} {bar} {_fmt_tokens(model.total_tokens)}")
        legend.append(f"{color}█{RESET} {model.model}")

    return ChartOutput(lines=lines, legend=legend)


def generateXAxisLabels(width: int = 40, max_val: int = 0) -> str:
    """Generate x-axis labels for a bar chart."""
    if max_val <= 0:
        return ""
    mid = _fmt_tokens(max_val // 2)
    end = _fmt_tokens(max_val)
    return f"  {'0'.ljust(width // 2)}{mid.center(width // 4)}{end.rjust(width // 4)}"


def renderOverviewToAnsi(stats: StatsResult) -> str:
    """Render overview statistics as ANSI text."""
    output: list[str] = []

    # Title
    label = DATE_RANGE_LABELS.get(stats.date_range, stats.date_range)
    output.append(f"  {BOLD}{CYAN}Usage Statistics — {label}{RESET}")
    output.append(_divider())
    output.append("")

    # Summary
    output.append(_row("Total tokens", f"{BOLD}{_fmt_tokens(stats.total_tokens)}{RESET}"))
    output.append(_row("  Input", _fmt_tokens(stats.total_input)))
    output.append(_row("  Output", _fmt_tokens(stats.total_output)))
    if stats.total_cache_read:
        output.append(_row("  Cache read", _fmt_tokens(stats.total_cache_read)))
    if stats.total_cache_write:
        output.append(_row("  Cache write", _fmt_tokens(stats.total_cache_write)))
    output.append("")

    output.append(_row("Total cost", f"{BOLD}{_fmt_cost(stats.total_cost)}{RESET}"))
    output.append(_row("API calls", str(stats.total_calls)))

    if stats.duration_seconds > 0:
        output.append(_row("Duration", _fmt_duration(stats.duration_seconds)))
        if stats.total_tokens > 0:
            tps = stats.total_tokens / stats.duration_seconds
            output.append(_row("Tokens/sec", f"{tps:.0f}"))

    output.append("")

    # Lines changed
    if stats.lines_added or stats.lines_removed:
        output.append(_row("Lines changed", ""))
        output.append(_row("  Added", f"{GREEN}+{stats.lines_added}{RESET}"))
        output.append(_row("  Removed", f"{RED}-{stats.lines_removed}{RESET}"))
        output.append("")

    # Tool calls
    if stats.tool_counts:
        output.append(f"  {BOLD}Tool Calls{RESET}")
        output.append(_divider(30))
        total_tools = sum(stats.tool_counts.values())
        max_count = max(stats.tool_counts.values()) if stats.tool_counts else 1

        for tool, count in sorted(stats.tool_counts.items(), key=lambda x: -x[1]):
            bar = _bar(count, max_count, width=15, color=BLUE)
            pct = (count / total_tools * 100) if total_tools else 0
            output.append(f"  {tool:<16} {bar} {count:>4} ({pct:.0f}%)")
        output.append("")

    return "\n".join(output)


def renderModelsToAnsi(stats: StatsResult) -> str:
    """Render per-model statistics as ANSI text."""
    output: list[str] = []

    if not stats.models:
        output.append(f"  {DIM}No model usage data.{RESET}")
        return "\n".join(output)

    output.append(f"  {BOLD}{CYAN}Models{RESET}")
    output.append(_divider())
    output.append("")

    # Token chart
    chart = generateTokenChart(stats)
    output.extend(chart.lines)
    output.append("")

    # Per-model details
    for model in sorted(stats.models, key=lambda m: -m.total_tokens):
        output.append(ModelEntry(model))
        output.append("")

    return "\n".join(output)


def ModelEntry(usage: ModelUsage) -> str:
    """Render a single model's usage stats."""
    output: list[str] = []
    output.append(f"  {BOLD}{usage.model}{RESET}")

    # Token breakdown
    parts = []
    if usage.input_tokens:
        parts.append(f"in:{_fmt_tokens(usage.input_tokens)}")
    if usage.output_tokens:
        parts.append(f"out:{_fmt_tokens(usage.output_tokens)}")
    if usage.cache_read:
        parts.append(f"cache_r:{_fmt_tokens(usage.cache_read)}")
    if usage.cache_write:
        parts.append(f"cache_w:{_fmt_tokens(usage.cache_write)}")
    if parts:
        output.append(f"    {DIM}{' | '.join(parts)}{RESET}")

    # Cost and calls
    extras = []
    if usage.cost > 0:
        extras.append(_fmt_cost(usage.cost))
    if usage.calls > 0:
        extras.append(f"{usage.calls} calls")
    if extras:
        output.append(f"    {DIM}{' · '.join(extras)}{RESET}")

    return "\n".join(output)


def renderStatsToAnsi(stats: StatsResult, tab: str = "overview") -> str:
    """Render statistics as ANSI text.

    Args:
        stats: Aggregated statistics.
        tab: Which tab to render: 'overview' or 'models'.

    Returns:
        ANSI-formatted string.
    """
    output: list[str] = []
    output.append("")

    if tab == "models":
        output.append(renderModelsToAnsi(stats))
    else:
        output.append(renderOverviewToAnsi(stats))
        if stats.models:
            output.append("")
            output.append(renderModelsToAnsi(stats))

    return "\n".join(output)


def OverviewTab(stats: StatsResult) -> str:
    """Render overview tab."""
    return renderOverviewToAnsi(stats)


def Stats(stats: Optional[StatsResult] = None, **kwargs) -> str:
    """Primary entry point for stats rendering."""
    if stats is None:
        return f"  {DIM}No statistics available.{RESET}"
    return renderStatsToAnsi(stats, **kwargs)


def StatsContent(stats: Optional[StatsResult] = None, **kwargs) -> str:
    """Render stats content."""
    return Stats(stats, **kwargs)


def ModelsTab(stats: StatsResult) -> str:
    """Render models tab."""
    return renderModelsToAnsi(stats)


def generateFunFactoid(stats: StatsResult) -> str:
    """Generate a fun factoid from the stats."""
    if not stats.total_tokens:
        return ""

    factoids = []
    if stats.total_tokens > 1_000_000:
        books = stats.total_tokens / 70000
        factoids.append(f"That's roughly {books:.0f} books worth of text!")
    if stats.lines_added > 1000:
        factoids.append(f"You've added {stats.lines_added:,} lines of code!")
    if stats.total_cost > 10:
        coffees = stats.total_cost / 5
        factoids.append(f"That's about {coffees:.0f} cups of coffee in API costs.")
    if stats.total_calls > 100:
        factoids.append(f"{stats.total_calls:,} API calls and counting!")

    if factoids:
        import random
        return f"  {ITALIC}{DIM}{random.choice(factoids)}{RESET}"
    return ""
