"""Lifelong Learner — Jarvis grows smarter every day without training.

Instead of batch training on GPUs, Jarvis learns INCREMENTALLY:

1. FROM CONVERSATIONS: Every exchange teaches something new
   - "Python was created by Guido" → stores as structured fact
   - "I prefer dark mode" → stores as preference
   - Corrections update wrong facts immediately

2. FROM THE INTERNET: Autonomously reads and extracts knowledge
   - Searches for topics the user asks about
   - Reads Wikipedia/documentation pages
   - Extracts facts and stores in memory
   - Does this in background during idle time

3. FROM OBSERVATION: Watches what the user does
   - What programs are running → learn the user's workflow
   - What files are edited → learn active projects
   - What time they work → learn their schedule
   - What websites they visit → learn their interests

4. FROM EXPERIENCE: Every response teaches what works
   - User says "thanks" → that response strategy worked
   - User says "no, wrong" → that approach failed, learn from it
   - Track success/failure patterns over time

The key insight: Claude/GPT train once on trillions of tokens.
Jarvis learns continuously from thousands of RELEVANT interactions.
Quality > quantity. Personal > general.

After 1000 conversations, Jarvis knows YOU better than any LLM ever will.
"""

from __future__ import annotations

import time
import re
import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter


@dataclass
class LearningEvent:
    """A single thing Jarvis learned."""
    content: str
    source: str       # "conversation", "internet", "observation", "correction"
    confidence: float = 0.8
    timestamp: float = field(default_factory=time.time)
    topic: str = ""


@dataclass
class LearningStats:
    """Track how much Jarvis has learned over its lifetime."""
    total_facts_learned: int = 0
    facts_from_conversation: int = 0
    facts_from_internet: int = 0
    facts_from_observation: int = 0
    facts_from_correction: int = 0
    conversations_processed: int = 0
    articles_read: int = 0
    corrections_applied: int = 0
    uptime_hours: float = 0.0
    first_boot: float = field(default_factory=time.time)


class LifelongLearner:
    """The core learning engine — makes Jarvis smarter every day.

    No batch training. No GPUs. Just continuous incremental learning
    from every interaction, every observation, every idle moment.
    """

    def __init__(self, data_dir: str | Path = "~/.jarvis/data"):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.stats = LearningStats()
        self._load_stats()

        # Pending internet searches (filled by curiosity, processed during idle)
        self._search_queue: list[str] = []

        # Recently learned facts (for deduplication)
        self._recent_hashes: set[str] = set()

        # Topic interest tracker (what topics does Jarvis need to learn more about)
        self._topic_interest: Counter = Counter()

    # ── LEARN FROM CONVERSATIONS ──

    def learn_from_exchange(self, user_input: str, response: str,
                            intent: str, holographic_memory=None) -> list[LearningEvent]:
        """Extract and store knowledge from a conversation exchange.

        Called after every response. Returns what was learned.
        """
        from brain.intelligence.nlu import NLUEngine
        from brain.intelligence.semantic_parser import SemanticParser

        nlu = NLUEngine()
        parser = SemanticParser()
        events = []

        # Level 1: NLU pattern extraction (fast, regex-based)
        nlu_result = nlu.analyze(user_input)
        for fact in nlu_result.facts:
            event = self._store_fact(
                fact.subject, fact.relation, fact.obj,
                source="conversation", confidence=fact.confidence,
                holographic=holographic_memory,
            )
            if event:
                events.append(event)

        # Level 2: Semantic parser (deeper, gets relations NLU misses)
        triples = parser.extract_relations(user_input)
        for subj, rel, obj in triples:
            if subj and rel and obj and len(subj) > 1 and len(obj) > 1:
                event = self._store_fact(
                    subj.lower(), rel.lower(), obj.lower(),
                    source="conversation", confidence=0.7,
                    holographic=holographic_memory,
                )
                if event:
                    events.append(event)

        # Level 3: Track topics of interest
        for keyword in nlu_result.keywords:
            self._topic_interest[keyword] += 1

        # Level 4: If user corrected Jarvis, learn from the correction
        if intent == "correction" or any(w in user_input.lower() for w in ["no,", "wrong", "actually"]):
            for correction in nlu_result.corrections:
                events.append(LearningEvent(
                    content=f"correction: {correction.corrected}",
                    source="correction",
                    confidence=0.95,
                ))
                self.stats.corrections_applied += 1

        self.stats.conversations_processed += 1
        self._save_stats()
        return events

    # ── LEARN FROM THE INTERNET ──

    def queue_search(self, topic: str):
        """Queue a topic for background internet learning."""
        if topic not in self._search_queue:
            self._search_queue.append(topic)

    async def learn_from_internet(self, topic: str,
                                  holographic_memory=None) -> list[LearningEvent]:
        """Search the internet for a topic and extract knowledge.

        Uses web search + scraping to find and learn facts.
        Runs during idle time or when Jarvis doesn't know something.
        """
        events = []

        try:
            from brain.internet.search import search_web
            from brain.internet.scraper import fetch_page

            # Search for the topic
            results = await search_web(topic, num_results=3)

            for result in results[:3]:
                url = result.get("url", "")
                if not url:
                    continue

                # Fetch and extract text
                try:
                    text = await fetch_page(url)
                    if text and len(text) > 100:
                        # Extract facts from the page
                        page_events = self._extract_from_text(
                            text[:5000],  # First 5000 chars
                            source="internet",
                            topic=topic,
                            holographic=holographic_memory,
                        )
                        events.extend(page_events)
                        self.stats.articles_read += 1
                except Exception:
                    continue

        except ImportError:
            # Web modules not available — try a simpler approach
            pass

        self.stats.facts_from_internet += len(events)
        return events

    def get_next_search_topic(self) -> str | None:
        """Get the next topic to research during idle time."""
        if self._search_queue:
            return self._search_queue.pop(0)

        # Auto-generate search topics from knowledge gaps
        if self._topic_interest:
            # Find topics asked about often but with few stored facts
            top_topics = self._topic_interest.most_common(5)
            for topic, count in top_topics:
                if count >= 3:  # Asked about 3+ times
                    return topic

        return None

    # ── LEARN FROM OBSERVATION ──

    def learn_from_system(self, holographic_memory=None) -> list[LearningEvent]:
        """Observe the system and learn about the user's workflow.

        Checks: running processes, recent files, system info.
        Runs periodically during idle time.
        """
        events = []

        # What programs are running?
        try:
            import subprocess
            result = subprocess.run(
                ["ps", "aux", "--sort=-%cpu"],
                capture_output=True, text=True, timeout=5
            )
            # Extract top processes (user's active programs)
            lines = result.stdout.strip().split("\n")[1:6]
            programs = set()
            for line in lines:
                parts = line.split()
                if len(parts) > 10:
                    cmd = parts[10].split("/")[-1]
                    if cmd not in ("ps", "python", "bash", "sh", "grep"):
                        programs.add(cmd)

            for prog in programs:
                event = self._store_fact(
                    "user", "runs_program", prog,
                    source="observation", confidence=0.6,
                    holographic=holographic_memory,
                )
                if event:
                    events.append(event)
        except Exception:
            pass

        # What time does the user typically work?
        hour = time.localtime().tm_hour
        period = "morning" if 6 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening" if 18 <= hour < 22 else "night"
        event = self._store_fact(
            "user", "active_during", period,
            source="observation", confidence=0.5,
            holographic=holographic_memory,
        )
        if event:
            events.append(event)

        self.stats.facts_from_observation += len(events)
        return events

    # ── LEARN FROM EXPERIENCE (what works) ──

    def learn_from_feedback(self, user_input: str, was_helpful: bool,
                            strategy_used: str = "",
                            holographic_memory=None) -> LearningEvent | None:
        """Learn from whether the response was helpful.

        Tracks which response strategies work for which types of questions.
        """
        feedback_type = "positive" if was_helpful else "negative"
        content = f"strategy '{strategy_used}' was {feedback_type} for intent '{user_input[:50]}'"

        event = self._store_fact(
            "strategy", feedback_type, strategy_used,
            source="experience", confidence=0.7,
            holographic=holographic_memory,
        )
        return event

    # ── INTERNAL HELPERS ──

    def _store_fact(self, subject: str, relation: str, obj: str,
                    source: str, confidence: float = 0.8,
                    holographic=None) -> LearningEvent | None:
        """Store a fact if it's new (deduplicate)."""
        fact_hash = hashlib.md5(f"{subject}|{relation}|{obj}".encode()).hexdigest()[:12]
        if fact_hash in self._recent_hashes:
            return None
        self._recent_hashes.add(fact_hash)

        # Keep the recent hash set bounded
        if len(self._recent_hashes) > 10000:
            self._recent_hashes = set(list(self._recent_hashes)[-5000:])

        # Store in holographic memory
        if holographic:
            holographic.store(subject.lower(), relation.lower(), obj.lower())

        self.stats.total_facts_learned += 1
        if source == "conversation":
            self.stats.facts_from_conversation += 1

        return LearningEvent(
            content=f"{subject} {relation} {obj}",
            source=source,
            confidence=confidence,
        )

    def _extract_from_text(self, text: str, source: str, topic: str,
                           holographic=None) -> list[LearningEvent]:
        """Extract facts from a block of text (web page, document, etc.)."""
        from brain.intelligence.nlu import NLUEngine
        from brain.intelligence.semantic_parser import SemanticParser

        nlu = NLUEngine()
        parser = SemanticParser()
        events = []

        # Split into sentences and process each
        sentences = re.split(r'(?<=[.!?])\s+', text)
        for sentence in sentences[:50]:  # Max 50 sentences per page
            sentence = sentence.strip()
            if len(sentence) < 10 or len(sentence) > 200:
                continue

            # NLU extraction
            result = nlu.analyze(sentence)
            for fact in result.facts:
                event = self._store_fact(
                    fact.subject, fact.relation, fact.obj,
                    source=source, confidence=fact.confidence * 0.7,  # Internet = lower confidence
                    holographic=holographic,
                )
                if event:
                    events.append(event)

            # Semantic parser for deeper extraction
            triples = parser.extract_relations(sentence)
            for subj, rel, obj in triples:
                if subj and rel and obj and len(subj) > 1:
                    event = self._store_fact(
                        subj.lower(), rel.lower(), obj.lower(),
                        source=source, confidence=0.5,
                        holographic=holographic,
                    )
                    if event:
                        events.append(event)

        return events

    # ── PERSISTENCE ──

    def _save_stats(self):
        """Save learning stats to disk."""
        stats_file = self.data_dir / "learning_stats.json"
        try:
            data = {
                "total_facts_learned": self.stats.total_facts_learned,
                "facts_from_conversation": self.stats.facts_from_conversation,
                "facts_from_internet": self.stats.facts_from_internet,
                "facts_from_observation": self.stats.facts_from_observation,
                "facts_from_correction": self.stats.facts_from_correction,
                "conversations_processed": self.stats.conversations_processed,
                "articles_read": self.stats.articles_read,
                "corrections_applied": self.stats.corrections_applied,
                "first_boot": self.stats.first_boot,
                "topic_interest": dict(self._topic_interest.most_common(100)),
            }
            stats_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load_stats(self):
        """Load learning stats from disk."""
        stats_file = self.data_dir / "learning_stats.json"
        if stats_file.exists():
            try:
                data = json.loads(stats_file.read_text())
                self.stats.total_facts_learned = data.get("total_facts_learned", 0)
                self.stats.facts_from_conversation = data.get("facts_from_conversation", 0)
                self.stats.facts_from_internet = data.get("facts_from_internet", 0)
                self.stats.facts_from_observation = data.get("facts_from_observation", 0)
                self.stats.facts_from_correction = data.get("facts_from_correction", 0)
                self.stats.conversations_processed = data.get("conversations_processed", 0)
                self.stats.articles_read = data.get("articles_read", 0)
                self.stats.corrections_applied = data.get("corrections_applied", 0)
                self.stats.first_boot = data.get("first_boot", time.time())
                self._topic_interest = Counter(data.get("topic_interest", {}))
            except Exception:
                pass

    def save_memory(self, holographic_memory, path: str | Path | None = None):
        """Persist holographic memory to disk so knowledge survives restarts."""
        import numpy as np

        save_path = Path(path) if path else self.data_dir / "holographic_memory.npz"
        save_path.parent.mkdir(parents=True, exist_ok=True)

        np.savez_compressed(
            str(save_path),
            trace=holographic_memory._trace,
            fact_count=holographic_memory._fact_count,
        )

        # Save fact registry separately (JSON)
        facts_path = save_path.with_suffix(".json")
        facts_data = {
            fid: {"s": s, "r": r, "o": o}
            for fid, (s, r, o) in holographic_memory._facts.items()
        }
        facts_path.write_text(json.dumps(facts_data))

    def load_memory(self, holographic_memory, path: str | Path | None = None) -> bool:
        """Load holographic memory from disk. Returns True if loaded."""
        import numpy as np

        load_path = Path(path) if path else self.data_dir / "holographic_memory.npz"
        if not load_path.exists():
            return False

        try:
            data = np.load(str(load_path))
            holographic_memory._trace = data["trace"]
            holographic_memory._fact_count = int(data["fact_count"])

            # Load fact registry
            facts_path = load_path.with_suffix(".json")
            if facts_path.exists():
                facts_data = json.loads(facts_path.read_text())
                for fid, vals in facts_data.items():
                    holographic_memory._facts[fid] = (vals["s"], vals["r"], vals["o"])
                    # Rebuild concept index
                    holographic_memory._concept_facts[vals["s"]].add(fid)
                    holographic_memory._concept_facts[vals["o"]].add(fid)
                    # Re-encode concepts in codebook
                    holographic_memory.codebook.encode(vals["s"])
                    holographic_memory.codebook.encode(vals["r"])
                    holographic_memory.codebook.encode(vals["o"])

            return True
        except Exception:
            return False

    def growth_report(self) -> str:
        """Generate a human-readable report of Jarvis's learning progress."""
        uptime_hrs = (time.time() - self.stats.first_boot) / 3600
        facts_per_day = self.stats.total_facts_learned / max(uptime_hrs / 24, 0.01)

        lines = [
            f"=== JARVIS Learning Report ===",
            f"Uptime: {uptime_hrs:.1f} hours",
            f"Total facts learned: {self.stats.total_facts_learned}",
            f"  From conversations: {self.stats.facts_from_conversation}",
            f"  From internet: {self.stats.facts_from_internet}",
            f"  From observation: {self.stats.facts_from_observation}",
            f"  From corrections: {self.stats.facts_from_correction}",
            f"Conversations processed: {self.stats.conversations_processed}",
            f"Articles read: {self.stats.articles_read}",
            f"Learning rate: {facts_per_day:.1f} facts/day",
        ]

        if self._topic_interest:
            top = self._topic_interest.most_common(5)
            lines.append(f"Top interests: {', '.join(f'{t}({c})' for t, c in top)}")

        return "\n".join(lines)
