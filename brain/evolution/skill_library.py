"""Voyager-Style Skill Library — auto-extract reusable skills from successful tasks.

After every successful multi-step agent task, JARVIS:
1. Extracts the solution as a reusable skill (code + description + embedding)
2. Stores it in a searchable library indexed by description
3. Retrieves relevant skills at planning time to avoid re-solving solved problems
4. Composes skills hierarchically (skills can reference other skills)

Inspired by Voyager (Minecraft agent) and Tool-R0 (self-curriculum).
"""

import json
import time
import logging
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from brain.config import DATA_DIR

log = logging.getLogger("jarvis.evolution.skills")

SKILL_LIBRARY_FILE = DATA_DIR / "skill_library.json"


@dataclass
class LearnedSkill:
    """A skill extracted from a successful agent task."""
    id: str                          # Hash of description
    name: str                        # Short name (e.g. "git_commit_with_message")
    description: str                 # What this skill does
    code: str                        # Python code or shell commands that implement it
    skill_type: str                  # "bash", "python", "agent_plan", "tool_sequence"
    tags: list[str] = field(default_factory=list)
    success_count: int = 0           # Times used successfully
    fail_count: int = 0              # Times used and failed
    created_at: float = 0
    last_used: float = 0
    source_task: str = ""            # Original task that created this skill
    dependencies: list[str] = field(default_factory=list)  # IDs of skills this depends on

    @property
    def score(self) -> float:
        """Reliability score: success rate weighted by recency."""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.5  # Unknown reliability
        success_rate = self.success_count / total
        # Recency bonus: skills used recently get a boost
        age_hours = (time.time() - self.last_used) / 3600 if self.last_used else 1000
        recency = max(0.1, 1.0 - (age_hours / 720))  # Decays over 30 days
        return 0.7 * success_rate + 0.3 * recency


class SkillLibrary:
    """Persistent, searchable library of learned skills."""

    def __init__(self):
        self._skills: dict[str, LearnedSkill] = {}
        self._load()

    def _load(self):
        """Load skill library from disk."""
        if SKILL_LIBRARY_FILE.exists():
            try:
                data = json.loads(SKILL_LIBRARY_FILE.read_text())
                for sid, sdata in data.items():
                    self._skills[sid] = LearnedSkill(**sdata)
                log.info("Loaded %d skills from library", len(self._skills))
            except Exception as e:
                log.warning("Failed to load skill library: %s", e)

    def _save(self):
        """Persist skill library to disk."""
        SKILL_LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {sid: asdict(s) for sid, s in self._skills.items()}
        SKILL_LIBRARY_FILE.write_text(json.dumps(data, indent=2))

    def add_skill(self, name: str, description: str, code: str,
                  skill_type: str = "python", tags: list[str] = None,
                  source_task: str = "", dependencies: list[str] = None) -> LearnedSkill:
        """Add a new skill to the library."""
        sid = hashlib.sha256(description.lower().encode()).hexdigest()[:16]

        # Update existing skill if it already exists
        if sid in self._skills:
            existing = self._skills[sid]
            existing.code = code
            existing.success_count += 1
            existing.last_used = time.time()
            self._save()
            return existing

        skill = LearnedSkill(
            id=sid,
            name=name,
            description=description,
            code=code,
            skill_type=skill_type,
            tags=tags or [],
            success_count=1,
            created_at=time.time(),
            last_used=time.time(),
            source_task=source_task,
            dependencies=dependencies or [],
        )
        self._skills[sid] = skill
        self._save()
        log.info("Added skill: %s (%s)", name, sid)
        return skill

    def search(self, query: str, top_k: int = 5) -> list[LearnedSkill]:
        """Search for relevant skills by keyword matching.

        Uses term frequency matching against description, name, and tags.
        For better results, integrate with embedding search.
        """
        query_terms = set(query.lower().split())
        scored = []

        for skill in self._skills.values():
            # Build searchable text
            text = f"{skill.name} {skill.description} {' '.join(skill.tags)}".lower()
            text_terms = set(text.split())

            # Term overlap score
            overlap = len(query_terms & text_terms)
            if overlap == 0:
                continue

            # Weighted: overlap * reliability * recency
            match_score = overlap / max(len(query_terms), 1)
            final_score = match_score * skill.score
            scored.append((final_score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_k]]

    def record_success(self, skill_id: str):
        """Record that a skill was used successfully."""
        if skill_id in self._skills:
            self._skills[skill_id].success_count += 1
            self._skills[skill_id].last_used = time.time()
            self._save()

    def record_failure(self, skill_id: str):
        """Record that a skill failed when used."""
        if skill_id in self._skills:
            self._skills[skill_id].fail_count += 1
            self._save()

    def remove_skill(self, skill_id: str) -> bool:
        """Remove a skill from the library."""
        if skill_id in self._skills:
            del self._skills[skill_id]
            self._save()
            return True
        return False

    def prune(self, min_score: float = 0.2, max_age_days: int = 90):
        """Remove low-quality or stale skills."""
        cutoff = time.time() - (max_age_days * 86400)
        to_remove = []
        for sid, skill in self._skills.items():
            if skill.score < min_score and skill.last_used < cutoff:
                to_remove.append(sid)
        for sid in to_remove:
            del self._skills[sid]
        if to_remove:
            self._save()
            log.info("Pruned %d stale skills", len(to_remove))

    def get_context_for_task(self, task_description: str, max_skills: int = 3) -> str:
        """Get relevant skills formatted for injection into agent system prompt."""
        relevant = self.search(task_description, top_k=max_skills)
        if not relevant:
            return ""

        parts = ["Previously learned skills relevant to this task:"]
        for skill in relevant:
            parts.append(f"\n### {skill.name} (reliability: {skill.score:.0%})")
            parts.append(f"Description: {skill.description}")
            parts.append(f"```{skill.skill_type}\n{skill.code[:500]}\n```")
        return "\n".join(parts)

    def stats(self) -> dict:
        """Library statistics."""
        if not self._skills:
            return {"total": 0}
        scores = [s.score for s in self._skills.values()]
        return {
            "total": len(self._skills),
            "avg_score": sum(scores) / len(scores),
            "types": dict(sorted(
                {t: sum(1 for s in self._skills.values() if s.skill_type == t)
                 for t in set(s.skill_type for s in self._skills.values())}.items()
            )),
        }

    async def extract_from_agent_run(self, task: str, tool_calls: list[dict],
                                      final_result: str, reasoner=None) -> LearnedSkill | None:
        """Extract a reusable skill from a successful agent run.

        Uses the LLM to summarize the tool call sequence into a reusable pattern.
        """
        if not tool_calls or len(tool_calls) < 2:
            return None  # Too simple to be worth saving

        # Build a summary of what happened
        steps = []
        for tc in tool_calls:
            name = tc.get("name", tc.get("function", {}).get("name", "unknown"))
            args = tc.get("arguments", tc.get("function", {}).get("arguments", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
            steps.append(f"- {name}({json.dumps(args)[:200]})")

        steps_text = "\n".join(steps[:20])

        if reasoner:
            # Use LLM to extract a clean skill
            prompt = (
                f"A task was completed successfully.\n"
                f"Task: {task}\n"
                f"Steps taken:\n{steps_text}\n"
                f"Result: {final_result[:300]}\n\n"
                f"Extract this as a reusable skill. Return ONLY a JSON object:\n"
                f'{{"name": "short_snake_case_name", '
                f'"description": "what this skill does in one sentence", '
                f'"code": "the key commands or code to reproduce this", '
                f'"type": "bash|python|tool_sequence", '
                f'"tags": ["tag1", "tag2"]}}'
            )
            try:
                response = await reasoner.query(
                    prompt,
                    system_prompt="Extract reusable skills from task completions. Return ONLY valid JSON.",
                    history=None,
                )
                # Parse JSON from response
                response = response.strip()
                if response.startswith("```"):
                    lines = response.split("\n")
                    response = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                data = json.loads(response)
                return self.add_skill(
                    name=data.get("name", "unnamed_skill"),
                    description=data.get("description", task[:100]),
                    code=data.get("code", steps_text),
                    skill_type=data.get("type", "tool_sequence"),
                    tags=data.get("tags", []),
                    source_task=task,
                )
            except Exception as e:
                log.debug("LLM skill extraction failed: %s", e)

        # Fallback: save raw tool sequence
        safe_name = "_".join(task.lower().split()[:4])
        safe_name = "".join(c if c.isalnum() or c == "_" else "" for c in safe_name)
        return self.add_skill(
            name=safe_name or "learned_task",
            description=task[:200],
            code=steps_text,
            skill_type="tool_sequence",
            tags=["auto-extracted"],
            source_task=task,
        )
