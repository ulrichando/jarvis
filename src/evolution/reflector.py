"""Reflexion Engine — learn from failures without fine-tuning.

When a task fails:
1. Generate a natural-language critique of what went wrong
2. Store the critique keyed by task type
3. On similar future tasks, inject the critique as context
4. Achieved 91% on HumanEval with zero weight updates (Shinn et al. 2023)

Also handles success reflection — extracting what worked for reuse.
"""

import json
import time
import logging
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from src.config import DATA_DIR

log = logging.getLogger("jarvis.evolution.reflector")

REFLECTIONS_FILE = DATA_DIR / "reflections.json"


@dataclass
class Reflection:
    """A lesson learned from success or failure."""
    id: str
    task: str                   # What was attempted
    outcome: str                # "success" or "failure"
    critique: str               # What went wrong / what worked
    fix_hint: str               # How to do better next time
    tags: list[str] = field(default_factory=list)
    created_at: float = 0
    times_retrieved: int = 0    # How many times this helped
    confirmed_helpful: int = 0  # How many times retrieval led to success


class Reflector:
    """Generates and retrieves reflections from task outcomes."""

    def __init__(self):
        self._reflections: dict[str, Reflection] = {}
        self._load()

    def _load(self):
        if REFLECTIONS_FILE.exists():
            try:
                data = json.loads(REFLECTIONS_FILE.read_text())
                for rid, rdata in data.items():
                    self._reflections[rid] = Reflection(**rdata)
                log.info("Loaded %d reflections", len(self._reflections))
            except Exception as e:
                log.warning("Failed to load reflections: %s", e)

    def _save(self):
        REFLECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {rid: asdict(r) for rid, r in self._reflections.items()}
        REFLECTIONS_FILE.write_text(json.dumps(data, indent=2))

    async def reflect_on_failure(self, task: str, error: str,
                                  tool_calls: list[dict] = None,
                                  reasoner=None) -> Reflection:
        """Generate a reflection after a failed task."""
        critique = f"Failed: {error[:200]}"
        fix_hint = "Try a different approach."

        if reasoner:
            steps = ""
            if tool_calls:
                steps = "\nSteps attempted:\n" + "\n".join(
                    f"- {tc.get('name', '?')}({str(tc.get('arguments', ''))[:100]})"
                    for tc in tool_calls[:10]
                )
            prompt = (
                f"A task failed. Analyze what went wrong in 2-3 sentences.\n"
                f"Task: {task}\n"
                f"Error: {error[:500]}{steps}\n\n"
                f"Return ONLY a JSON object:\n"
                f'{{"critique": "what went wrong", "fix_hint": "how to succeed next time", '
                f'"tags": ["category1", "category2"]}}'
            )
            try:
                response = await reasoner.query(
                    prompt,
                    system_prompt="You are a debugging expert. Analyze failures concisely. Return ONLY valid JSON.",
                    history=None,
                )
                response = response.strip()
                if response.startswith("```"):
                    lines = response.split("\n")
                    response = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                data = json.loads(response)
                critique = data.get("critique", critique)
                fix_hint = data.get("fix_hint", fix_hint)
                tags = data.get("tags", [])
            except Exception as e:
                log.debug("LLM reflection failed: %s", e)
                tags = ["auto"]
        else:
            tags = ["auto"]

        rid = hashlib.sha256(f"{task}:{error[:100]}".encode()).hexdigest()[:16]
        reflection = Reflection(
            id=rid,
            task=task[:500],
            outcome="failure",
            critique=critique,
            fix_hint=fix_hint,
            tags=tags,
            created_at=time.time(),
        )
        self._reflections[rid] = reflection
        self._save()
        log.info("Reflected on failure: %s", critique[:80])
        return reflection

    async def reflect_on_success(self, task: str, approach: str,
                                   reasoner=None) -> Reflection:
        """Generate a reflection after a successful task — what worked."""
        critique = f"Succeeded with: {approach[:200]}"
        fix_hint = "Use this approach again for similar tasks."

        if reasoner:
            prompt = (
                f"A task succeeded. Summarize the key insight in 1-2 sentences.\n"
                f"Task: {task}\n"
                f"Approach: {approach[:500]}\n\n"
                f"Return ONLY a JSON object:\n"
                f'{{"critique": "what worked and why", "fix_hint": "reusable insight", '
                f'"tags": ["category1"]}}'
            )
            try:
                response = await reasoner.query(
                    prompt,
                    system_prompt="Extract reusable insights from successes. Return ONLY valid JSON.",
                    history=None,
                )
                response = response.strip()
                if response.startswith("```"):
                    lines = response.split("\n")
                    response = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                data = json.loads(response)
                critique = data.get("critique", critique)
                fix_hint = data.get("fix_hint", fix_hint)
                tags = data.get("tags", [])
            except Exception:
                tags = ["auto"]
        else:
            tags = ["auto"]

        rid = hashlib.sha256(f"success:{task}".encode()).hexdigest()[:16]
        reflection = Reflection(
            id=rid,
            task=task[:500],
            outcome="success",
            critique=critique,
            fix_hint=fix_hint,
            tags=tags,
            created_at=time.time(),
        )
        self._reflections[rid] = reflection
        self._save()
        return reflection

    def get_relevant_reflections(self, task: str, top_k: int = 3) -> list[Reflection]:
        """Find reflections relevant to a new task."""
        query_terms = set(task.lower().split())
        scored = []

        for ref in self._reflections.values():
            text = f"{ref.task} {' '.join(ref.tags)}".lower()
            text_terms = set(text.split())
            overlap = len(query_terms & text_terms)
            if overlap == 0:
                continue
            # Failures are more valuable than successes for learning
            weight = 1.5 if ref.outcome == "failure" else 1.0
            # Confirmed helpful reflections get a boost
            helpful_bonus = 1.0 + (ref.confirmed_helpful * 0.2)
            score = (overlap / max(len(query_terms), 1)) * weight * helpful_bonus
            scored.append((score, ref))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in scored[:top_k]]

        # Track retrieval
        for r in results:
            r.times_retrieved += 1
        if results:
            self._save()

        return results

    def get_context_for_task(self, task: str, max_reflections: int = 3) -> str:
        """Format relevant reflections for injection into agent prompt."""
        relevant = self.get_relevant_reflections(task, top_k=max_reflections)
        if not relevant:
            return ""

        parts = ["Lessons from previous similar tasks:"]
        for ref in relevant:
            icon = "x" if ref.outcome == "failure" else "v"
            parts.append(f"\n[{icon}] Task: {ref.task[:100]}")
            parts.append(f"   Lesson: {ref.critique}")
            parts.append(f"   Hint: {ref.fix_hint}")
        return "\n".join(parts)

    def confirm_helpful(self, reflection_id: str):
        """Mark a reflection as confirmed helpful (task succeeded with it)."""
        if reflection_id in self._reflections:
            self._reflections[reflection_id].confirmed_helpful += 1
            self._save()

    def prune(self, max_age_days: int = 180):
        """Remove old, unhelpful reflections."""
        cutoff = time.time() - (max_age_days * 86400)
        to_remove = [
            rid for rid, r in self._reflections.items()
            if r.created_at < cutoff and r.confirmed_helpful == 0
        ]
        for rid in to_remove:
            del self._reflections[rid]
        if to_remove:
            self._save()
            log.info("Pruned %d stale reflections", len(to_remove))

    def stats(self) -> dict:
        total = len(self._reflections)
        if total == 0:
            return {"total": 0}
        failures = sum(1 for r in self._reflections.values() if r.outcome == "failure")
        return {
            "total": total,
            "failures": failures,
            "successes": total - failures,
            "confirmed_helpful": sum(r.confirmed_helpful for r in self._reflections.values()),
        }
