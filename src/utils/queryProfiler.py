"""
Query profiling utility for measuring time spent in the query pipeline.

Enable by setting QUERY_PROFILE=1 environment variable.
Tracks each query session with detailed checkpoints for identifying bottlenecks.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple

ENABLED = os.environ.get("QUERY_PROFILE", "").lower() in ("1", "true", "yes")

_checkpoints: List[Tuple[str, float]] = []
_query_count = 0
_first_token_time: Optional[float] = None


def start_query_profile() -> None:
    """Start profiling a new query session."""
    global _query_count, _first_token_time
    if not ENABLED:
        return

    _checkpoints.clear()
    _first_token_time = None
    _query_count += 1

    query_checkpoint("query_user_input_received")


def query_checkpoint(name: str) -> None:
    """Record a checkpoint with the given name."""
    global _first_token_time
    if not ENABLED:
        return

    now = time.perf_counter()
    _checkpoints.append((name, now))

    if name == "query_first_chunk_received" and _first_token_time is None:
        _first_token_time = now


def end_query_profile() -> None:
    """End the current query profiling session."""
    if not ENABLED:
        return
    query_checkpoint("query_profile_end")


def _format_ms(ms: float) -> str:
    """Format milliseconds with appropriate precision."""
    if ms < 1:
        return f"{ms:.3f}"
    if ms < 10:
        return f"{ms:.2f}"
    if ms < 100:
        return f"{ms:.1f}"
    return f"{ms:.0f}"


def _get_slow_warning(delta_ms: float, name: str) -> str:
    """Identify slow operations (> 100ms delta)."""
    if name == "query_user_input_received":
        return ""

    if delta_ms > 1000:
        return "  VERY SLOW"
    if delta_ms > 100:
        return "  SLOW"

    if "git_status" in name and delta_ms > 50:
        return "  git status"
    if "tool_schema" in name and delta_ms > 50:
        return "  tool schemas"
    if "client_creation" in name and delta_ms > 50:
        return "  client creation"

    return ""


def get_query_profile_report() -> str:
    """Get a formatted report of all checkpoints for the current/last query."""
    if not ENABLED:
        return "Query profiling not enabled (set QUERY_PROFILE=1)"

    if not _checkpoints:
        return "No query profiling checkpoints recorded"

    lines = []
    lines.append("=" * 80)
    lines.append(f"QUERY PROFILING REPORT - Query #{_query_count}")
    lines.append("=" * 80)
    lines.append("")

    baseline_time = _checkpoints[0][1]
    prev_time = baseline_time
    api_request_sent_time = 0.0
    first_chunk_time = 0.0

    for name, ts in _checkpoints:
        relative_ms = (ts - baseline_time) * 1000
        delta_ms = (ts - prev_time) * 1000
        warning = _get_slow_warning(delta_ms, name)

        lines.append(
            f"  {_format_ms(relative_ms):>10}ms  "
            f"+{_format_ms(delta_ms):>9}ms  "
            f"{name}{warning}"
        )

        if name == "query_api_request_sent":
            api_request_sent_time = relative_ms
        if name == "query_first_chunk_received":
            first_chunk_time = relative_ms

        prev_time = ts

    total_ms = (_checkpoints[-1][1] - baseline_time) * 1000

    lines.append("")
    lines.append("-" * 80)

    if first_chunk_time > 0:
        pre_request = api_request_sent_time
        network = first_chunk_time - api_request_sent_time
        pre_pct = (pre_request / first_chunk_time * 100) if first_chunk_time else 0
        net_pct = (network / first_chunk_time * 100) if first_chunk_time else 0

        lines.append(f"Total TTFT: {_format_ms(first_chunk_time)}ms")
        lines.append(
            f"  - Pre-request overhead: {_format_ms(pre_request)}ms ({pre_pct:.1f}%)"
        )
        lines.append(
            f"  - Network latency: {_format_ms(network)}ms ({net_pct:.1f}%)"
        )
    else:
        lines.append(f"Total time: {_format_ms(total_ms)}ms")

    lines.append("=" * 80)
    return "\n".join(lines)


def log_query_profile_report() -> None:
    """Log the query profile report."""
    if not ENABLED:
        return
    print(get_query_profile_report())
