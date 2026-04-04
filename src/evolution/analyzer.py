"""Evolution Analyzer — finds patterns in JARVIS usage to improve itself.

Analyzes telemetry data and identifies:
- Most common queries (optimize with shortcuts)
- Failed interactions (fix bugs)
- Slow responses (route to local model)
- Repeated multi-step workflows (create macros)
"""

from src.evolution.telemetry import Telemetry


class EvolutionAnalyzer:
    """Analyzes usage patterns to drive self-improvement."""

    def __init__(self, telemetry: Telemetry):
        self.telemetry = telemetry

    def analyze(self, days: int = 7) -> dict:
        """Run full analysis and return improvement opportunities."""
        common = self.telemetry.get_common_queries(days=days, limit=20)
        failed = self.telemetry.get_failed_interactions(days=days)
        avg_latency = self.telemetry.get_avg_latency(days=days)

        opportunities = []

        # High-frequency queries → create shortcuts
        for q in common:
            if q["count"] >= 5:
                opportunities.append({
                    "type": "shortcut",
                    "query": q["user_input"],
                    "count": q["count"],
                    "reason": f"Asked {q['count']} times — create a direct handler",
                })

        # Failed interactions → needs fixing
        for f in failed[:10]:
            opportunities.append({
                "type": "fix",
                "query": f["user_input"],
                "error": f.get("error", "unknown"),
                "reason": "This interaction failed — investigate and fix",
            })

        # Slow responses → consider local model
        if avg_latency > 2000:
            opportunities.append({
                "type": "performance",
                "avg_latency_ms": avg_latency,
                "reason": "Average latency > 2s — route simple queries locally",
            })

        return {
            "total_interactions": sum(q["count"] for q in common),
            "unique_queries": len(common),
            "failed_count": len(failed),
            "avg_latency_ms": round(avg_latency),
            "opportunities": opportunities,
        }

    def generate_report(self, days: int = 7) -> str:
        """Generate a human-readable analysis report."""
        data = self.analyze(days)
        lines = [
            f"=== JARVIS Evolution Report ({days} days) ===",
            f"Total interactions: {data['total_interactions']}",
            f"Unique queries: {data['unique_queries']}",
            f"Failed: {data['failed_count']}",
            f"Avg latency: {data['avg_latency_ms']}ms",
            "",
            f"Improvement opportunities: {len(data['opportunities'])}",
        ]
        for opp in data["opportunities"]:
            lines.append(f"  [{opp['type'].upper()}] {opp['reason']}")

        return "\n".join(lines)
