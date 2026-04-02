"""JARVIS Dreaming Engine — autonomous background processing during idle.

Like biological sleep, Jarvis's "dream state" performs critical maintenance:

1. REPLAY: Re-process recent conversations to extract missed knowledge
2. CONSOLIDATE: Merge similar facts, form higher-level concepts
3. PREDICT: Run the prediction engine forward to anticipate tomorrow's needs
4. STRENGTHEN: Reinforce frequently-used memory pathways
5. PRUNE: Let unused memories decay and remove dead ones
6. IMAGINE: Generate hypothetical scenarios to test reasoning ("what if...")
7. CURIOSITY: Identify knowledge gaps and prepare questions

Unlike human dreams, Jarvis's dreams are structured and productive.
They run as a background task when the user hasn't spoken for a while.

The dream cycle produces a "dream report" — a summary of what was
processed, what was learned, and what questions arose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DreamReport:
    """What happened during a dream cycle."""
    duration_ms: int = 0
    facts_consolidated: int = 0
    facts_strengthened: int = 0
    facts_pruned: int = 0
    concepts_formed: list[str] = field(default_factory=list)
    patterns_found: list[str] = field(default_factory=list)
    questions_generated: list[str] = field(default_factory=list)
    predictions: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)


class DreamEngine:
    """Runs Jarvis's dream cycle — autonomous background processing.

    Called when the user hasn't spoken for a configurable idle period.
    Can also be triggered manually: "Jarvis, take a moment to think."
    """

    def __init__(self):
        self._last_dream = 0.0
        self._dream_interval = 300.0  # Dream every 5 minutes of idle
        self._dream_count = 0
        self._idle_since = time.time()

    def should_dream(self) -> bool:
        """Check if it's time to dream (enough idle time has passed)."""
        now = time.time()
        idle_duration = now - self._idle_since
        since_last = now - self._last_dream
        return idle_duration > self._dream_interval and since_last > self._dream_interval

    def reset_idle(self):
        """Called when user speaks — reset idle timer."""
        self._idle_since = time.time()

    def dream(self,
              holographic_memory=None,
              associative_memory=None,
              consolidator=None,
              prediction_engine=None,
              emotion=None,
              learner=None,
              episodes: list[dict] | None = None) -> DreamReport:
        """Run one dream cycle.

        Processes all available subsystems. Each is optional —
        the dream adapts to whatever subsystems are available.
        """
        start = time.time()
        report = DreamReport()

        # Phase 1: CONSOLIDATE — merge duplicates, form concepts
        if consolidator and holographic_memory:
            self._dream_consolidate(consolidator, holographic_memory, episodes, report)

        # Phase 2: REPLAY — re-process recent episodes for missed facts
        if learner and episodes:
            self._dream_replay(learner, holographic_memory, episodes, report)

        # Phase 3: PREDICT — anticipate future needs
        if prediction_engine:
            self._dream_predict(prediction_engine, report)

        # Phase 4: STRENGTHEN / PRUNE — memory maintenance
        if associative_memory:
            self._dream_maintain(associative_memory, report)

        # Phase 5: IMAGINE — generate "what if" scenarios
        self._dream_imagine(holographic_memory, report)

        # Phase 6: CURIOSITY — identify knowledge gaps
        if holographic_memory:
            self._dream_curiosity(holographic_memory, report)

        # Phase 7: EMOTIONAL RESET — drift toward baseline
        if emotion:
            emotion.decay(self._dream_interval)

        report.duration_ms = int((time.time() - start) * 1000)
        self._last_dream = time.time()
        self._dream_count += 1

        return report

    def _dream_consolidate(self, consolidator, holographic, episodes, report):
        """Phase 1: Memory consolidation — merge, deduplicate, form concepts."""
        # Get all facts from holographic memory
        facts = []
        for fid, (s, r, o) in holographic._facts.items():
            facts.append(f"{s} {r} {o}")

        if not facts:
            return

        result = consolidator.consolidate(
            facts=facts,
            episodes=episodes or [],
            interaction_count=self._dream_count * 100,
        )

        report.facts_consolidated = len(result.merged_facts)
        report.concepts_formed = result.concepts
        report.patterns_found = [p["description"] for p in result.patterns[:5]]

        # Store new concepts in holographic memory
        for concept in result.concepts[:5]:
            holographic.store_text(concept)

        # Store promoted facts
        for new_fact in result.new_facts[:5]:
            holographic.store_text(new_fact)

    def _dream_replay(self, learner, holographic, episodes, report):
        """Phase 2: Replay recent conversations and extract missed facts."""
        replayed = 0
        for ep in episodes[-20:]:  # Replay last 20 episodes
            inp = ep.get("input", "")
            resp = ep.get("response", "")
            if inp:
                facts = learner.observe(inp, resp)
                for fact in facts:
                    if holographic:
                        holographic.store(fact.subject, fact.relation, fact.obj)
                    replayed += 1

        if replayed > 0:
            report.insights.append(f"Replayed {replayed} facts from recent conversations")

    def _dream_predict(self, prediction_engine, report):
        """Phase 3: Run prediction engine forward."""
        prediction = prediction_engine.predict()
        if prediction.confidence > 0.3:
            report.predictions.append(
                f"Next topic likely: {prediction.predicted_topic} "
                f"(conf={prediction.confidence:.2f})"
            )

    def _dream_maintain(self, associative, report):
        """Phase 4: Strengthen active paths, prune dead ones."""
        pruned = associative.decay_and_prune(min_strength=0.02)
        report.facts_pruned = pruned
        if pruned > 0:
            report.insights.append(f"Pruned {pruned} decayed memories")

    def _dream_imagine(self, holographic, report):
        """Phase 5: Generate hypothetical scenarios.

        "What if the user asks about X?" — pre-compute answers for likely questions.
        This is like mental rehearsal during sleep.
        """
        if not holographic or holographic.fact_count < 10:
            return

        # Generate hypothetical questions based on stored knowledge
        hypotheticals = [
            "what is the most important thing I know",
            "what should I learn next",
            "what patterns have I noticed",
        ]
        for q in hypotheticals:
            results = holographic.recall_text(q, top_k=1)
            if results:
                report.insights.append(
                    f"Rehearsed: '{q}' → '{results[0].content}'"
                )

    def _dream_curiosity(self, holographic, report):
        """Phase 6: Identify knowledge gaps.

        Find topics that are referenced but not well understood.
        Generate questions for the next conversation.
        """
        from brain.intelligence.nlu import GapDetector

        # Get all stored facts
        facts = [f"{s} {r} {o}" for _, (s, r, o) in holographic._facts.items()]
        if not facts:
            return

        detector = GapDetector()
        # Check for gaps in common topics
        for topic in ["user", "python", "security", "system"]:
            gaps = detector.detect_gaps(topic, facts)
            for gap in gaps[:1]:
                report.questions_generated.append(gap["question"])

    def stats(self) -> dict:
        return {
            "dreams_completed": self._dream_count,
            "last_dream": self._last_dream,
            "idle_since": self._idle_since,
            "idle_seconds": time.time() - self._idle_since,
            "next_dream_in": max(0, self._dream_interval - (time.time() - self._last_dream)),
        }
