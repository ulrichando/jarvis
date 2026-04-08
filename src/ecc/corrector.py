"""ECC Orchestrator — L1 Response Correction + L5 Metrics.

L1: After generating a standard (non-agent) response, score it.
    If quality < QUALITY_THRESHOLD, regenerate with targeted guidance.
    Maximum MAX_L1_ATTEMPTS regenerations per turn.

L5: Session-scoped metrics so we can see how hard ECC is working.
"""

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.ecc.corrector")

QUALITY_THRESHOLD = 0.50   # Below this → attempt regeneration
MAX_L1_ATTEMPTS   = 2      # Max regenerations per turn


@dataclass
class ECCMetrics:
    """Rolling session statistics for ECC activity."""
    l1_attempts:  int = 0
    l1_successes: int = 0
    l2_attempts:  int = 0
    l2_successes: int = 0
    l3_attempts:  int = 0
    l3_successes: int = 0
    session_start: float = field(default_factory=time.time)

    def report(self) -> str:
        elapsed = int(time.time() - self.session_start)
        return (
            f"ECC [{elapsed}s] "
            f"L1={self.l1_successes}/{self.l1_attempts} "
            f"L2={self.l2_successes}/{self.l2_attempts} "
            f"L3={self.l3_successes}/{self.l3_attempts}"
        )


class ECCorrector:
    """Wraps response generation with an optional quality-correction loop."""

    def __init__(self):
        self.metrics = ECCMetrics()

    # ── L1: Response correction ───────────────────────────────────────────

    async def correct_response(
        self,
        user_input: str,
        response: str,
        reasoner,
        quality_score: float,
        system_prompt: str,
    ) -> str:
        """If quality < threshold, regenerate up to MAX_L1_ATTEMPTS times.

        Returns the best response seen (original or corrected).
        Never raises — if correction fails we return the original.
        """
        if quality_score >= QUALITY_THRESHOLD:
            return response

        best = response
        best_quality = quality_score
        attempts = 0

        while best_quality < QUALITY_THRESHOLD and attempts < MAX_L1_ATTEMPTS:
            attempts += 1
            self.metrics.l1_attempts += 1

            issues = self._diagnose(user_input, best)
            correction_prompt = (
                f"Your previous response had issues: {issues}. "
                f"Give a better answer to: {user_input}"
            )
            log.info(
                "ECC-L1: attempt %d (quality=%.2f) — %s",
                attempts, best_quality, issues,
            )

            try:
                corrected = await reasoner.query(
                    correction_prompt,
                    system_prompt=system_prompt,
                    history=None,
                )
                new_quality = self._quick_score(user_input, corrected)
                if new_quality > best_quality:
                    best = corrected
                    best_quality = new_quality
                    self.metrics.l1_successes += 1
                    log.info(
                        "ECC-L1: improved %.2f → %.2f", quality_score, new_quality
                    )
                else:
                    break   # correction didn't help; stop
            except Exception as exc:
                log.debug("ECC-L1: regeneration failed: %s", exc)
                break

        return best

    # ── L5 helpers ────────────────────────────────────────────────────────

    def record_l2(self, success: bool):
        self.metrics.l2_attempts += 1
        if success:
            self.metrics.l2_successes += 1

    def record_l3(self, success: bool):
        self.metrics.l3_attempts += 1
        if success:
            self.metrics.l3_successes += 1

    def get_metrics(self) -> ECCMetrics:
        return self.metrics

    # ── Internal helpers ──────────────────────────────────────────────────

    def _diagnose(self, user_input: str, response: str) -> str:
        issues = []
        if not response.strip():
            issues.append("empty response")
            return "; ".join(issues)
        if "?" in user_input and len(response.strip()) < 15:
            issues.append("too short for a question")
        if any(p in response.lower() for p in [
            "i don't know", "i can't help", "i'm unable", "i cannot",
        ]):
            issues.append("refusal without attempt")
        if any(p in response.lower() for p in ["error:", "traceback", "exception:"]):
            issues.append("contains raw error output")
        if not issues:
            issues.append("low coherence or relevance")
        return "; ".join(issues)

    def _quick_score(self, user_input: str, response: str) -> float:
        """Fast heuristic quality score — no LLM needed."""
        if not response.strip():
            return 0.0
        score = 0.5
        if "?" in user_input and len(response.strip()) > 20:
            score += 0.15
        if any(p in response.lower() for p in [
            "i don't know", "i can't", "i'm unable",
        ]):
            score -= 0.25
        if len(response.split()) >= 5:
            score += 0.1
        if any(p in response.lower() for p in ["error:", "traceback"]):
            score -= 0.15
        return max(0.0, min(1.0, score))
