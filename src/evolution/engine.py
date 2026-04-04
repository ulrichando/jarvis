"""Evolution Engine — the self-improvement loop.

Architecture (SICA-inspired):
1. Analyze telemetry for improvement opportunities
2. Check skill library and reflections for prior solutions
3. Generate code improvements using best-of-archive strategy
4. Validate in subprocess sandbox with AST safety checks
5. Deploy if valid, rollback if broken
6. Score the result and archive for future iterations

Weighted utility: U = 0.5*score + 0.25*(1-cost) + 0.25*(1-time)
"""

import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from src.config import DATA_DIR
from src.evolution.telemetry import Telemetry
from src.evolution.analyzer import EvolutionAnalyzer
from src.evolution.generator import EvolutionGenerator
from src.evolution.validator import EvolutionValidator
from src.evolution.deployer import EvolutionDeployer
from src.evolution.skill_library import SkillLibrary
from src.evolution.reflector import Reflector

log = logging.getLogger("jarvis.evolution")

ARCHIVE_FILE = DATA_DIR / "evolution_archive.json"


@dataclass
class EvolutionSnapshot:
    """A scored snapshot of an evolution attempt."""
    timestamp: float
    opportunities: int
    code_generated: bool
    validated: bool
    deployed: bool
    score: float           # 0-1 utility score
    latency_before: float  # avg latency before
    latency_after: float   # avg latency after (0 if not measured yet)
    failures_fixed: int
    shortcuts_created: int


class EvolutionEngine:
    """The self-evolution loop with archive-based strategy selection."""

    def __init__(self, telemetry: Telemetry):
        self.telemetry = telemetry
        self.analyzer = EvolutionAnalyzer(telemetry)
        self.generator = EvolutionGenerator()
        self.validator = EvolutionValidator()
        self.deployer = EvolutionDeployer()
        self.skill_library = SkillLibrary()
        self.reflector = Reflector()
        self.last_run = 0
        self._archive: list[dict] = []
        self._load_archive()

    def _load_archive(self):
        if ARCHIVE_FILE.exists():
            try:
                self._archive = json.loads(ARCHIVE_FILE.read_text())
            except Exception:
                pass

    def _save_archive(self):
        ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Keep last 50 entries
        self._archive = self._archive[-50:]
        ARCHIVE_FILE.write_text(json.dumps(self._archive, indent=2))

    async def evolve(self, days: int = 7) -> dict:
        """Run one evolution cycle with archive tracking.

        1. Analyze usage patterns
        2. Check for relevant prior skills and reflections
        3. Generate code improvements
        4. Validate in sandbox
        5. Deploy if valid
        6. Score and archive the result
        """
        report = {"timestamp": time.time(), "steps": []}
        start_time = time.time()

        # Step 1: Analyze
        try:
            analysis = self.analyzer.analyze(days)
        except Exception as e:
            log.error("Evolution analysis failed: %s", e)
            report["result"] = "analysis_error"
            report["error"] = str(e)
            return report
        report["analysis"] = analysis
        report["steps"].append("analyzed")

        if not analysis["opportunities"]:
            report["result"] = "nothing_to_improve"
            return report

        # Step 2: Check skill library for existing solutions
        for opp in analysis["opportunities"]:
            if opp.get("query"):
                existing = self.skill_library.search(opp["query"], top_k=1)
                if existing and existing[0].score > 0.7:
                    opp["existing_skill"] = existing[0].name
                    log.info("Found existing skill for '%s': %s", opp["query"][:30], existing[0].name)

        # Check reflections for lessons from past failures
        reflection_context = ""
        for opp in analysis["opportunities"]:
            if opp["type"] == "fix" and opp.get("query"):
                refs = self.reflector.get_relevant_reflections(opp["query"], top_k=2)
                if refs:
                    reflection_context += f"\nPast lessons for '{opp['query'][:40]}':\n"
                    for r in refs:
                        reflection_context += f"  - {r.critique} → {r.fix_hint}\n"

        # Filter out opportunities that already have good skills
        remaining = [o for o in analysis["opportunities"] if "existing_skill" not in o]
        if not remaining:
            report["result"] = "all_solved_by_existing_skills"
            return report

        # Step 3: Generate improvements
        try:
            code = await self.generator.generate_shortcuts(remaining)
        except Exception as e:
            log.error("Evolution code generation failed: %s", e)
            await self.reflector.reflect_on_failure(
                "evolution code generation", str(e), reasoner=self.generator.reasoner
            )
            report["result"] = "generation_error"
            report["error"] = str(e)
            return report
        if not code:
            report["result"] = "no_code_generated"
            return report
        report["steps"].append("generated")

        # Step 4: Validate
        try:
            test_inputs = [o["query"] for o in remaining if o.get("query")]
            validation = self.validator.validate_module(code, test_inputs[:5])
        except Exception as e:
            log.error("Evolution validation failed: %s", e)
            await self.reflector.reflect_on_failure(
                "evolution validation", str(e), reasoner=self.generator.reasoner
            )
            report["result"] = "validation_error"
            report["error"] = str(e)
            return report
        report["validation"] = validation

        if not validation["valid"]:
            await self.reflector.reflect_on_failure(
                "evolution validation",
                "; ".join(validation["errors"]),
                reasoner=self.generator.reasoner,
            )
            report["result"] = "validation_failed"
            report["errors"] = validation["errors"]
            return report
        report["steps"].append("validated")

        # Step 5: Deploy
        try:
            deployment = self.deployer.deploy_shortcuts(code)
        except Exception as e:
            log.error("Evolution deployment failed: %s", e)
            report["result"] = "deployment_error"
            report["error"] = str(e)
            return report
        report["deployment"] = deployment
        report["steps"].append("deployed")

        # Step 6: Score and archive
        elapsed = time.time() - start_time
        score = self._calculate_score(
            opportunities_found=len(analysis["opportunities"]),
            opportunities_fixed=len(remaining),
            validation_errors=len(validation.get("errors", [])),
            elapsed_seconds=elapsed,
        )

        snapshot = {
            "timestamp": time.time(),
            "opportunities": len(analysis["opportunities"]),
            "fixed": len(remaining),
            "score": score,
            "elapsed_seconds": round(elapsed, 1),
            "result": "evolved",
        }
        self._archive.append(snapshot)
        self._save_archive()

        # Extract skills from what we generated
        await self.skill_library.extract_from_agent_run(
            task="evolution cycle",
            tool_calls=[{"name": "generate_shortcuts", "arguments": {"count": len(remaining)}}],
            final_result=f"Generated {len(remaining)} shortcuts",
            reasoner=self.generator.reasoner,
        )

        # Record success reflection
        await self.reflector.reflect_on_success(
            f"Evolution cycle: {len(remaining)} improvements",
            f"Analyzed {days}d of data, found {len(analysis['opportunities'])} opportunities, "
            f"deployed {len(remaining)} fixes. Score: {score:.2f}",
            reasoner=self.generator.reasoner,
        )

        self.last_run = time.time()
        report["result"] = "evolved"
        report["score"] = score
        return report

    def _calculate_score(self, opportunities_found: int, opportunities_fixed: int,
                         validation_errors: int, elapsed_seconds: float) -> float:
        """Weighted utility: U = 0.5*effectiveness + 0.25*(1-error_rate) + 0.25*(1-time_cost)"""
        if opportunities_found == 0:
            return 0.0

        effectiveness = opportunities_fixed / max(opportunities_found, 1)
        error_rate = validation_errors / max(opportunities_fixed, 1)
        time_cost = min(elapsed_seconds / 300, 1.0)  # Normalize to 5 min max

        return 0.5 * effectiveness + 0.25 * (1 - error_rate) + 0.25 * (1 - time_cost)

    def get_best_score(self) -> float:
        """Get the best evolution score from the archive."""
        if not self._archive:
            return 0.0
        return max(s.get("score", 0) for s in self._archive)

    def get_status(self) -> dict:
        """Get evolution engine status."""
        return {
            "last_run": self.last_run,
            "hours_since": round((time.time() - self.last_run) / 3600, 1) if self.last_run else None,
            "archive_size": len(self._archive),
            "best_score": self.get_best_score(),
            "skill_library": self.skill_library.stats(),
            "reflections": self.reflector.stats(),
        }
