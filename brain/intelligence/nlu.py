"""JARVIS Natural Language Understanding — local, no LLM required.

Extracts structured knowledge from natural language using:
1. Pattern templates for fact extraction ("X is Y", "the Y of X is Z")
2. Regex-based entity recognition (names, places, numbers, dates)
3. Preference detection ("I like/prefer/hate X")
4. Correction detection ("no, actually X is Y")
5. Relationship extraction ("X causes Y", "X is part of Y")

This replaces all external LLM calls in CuriosityEngine and
ConversationLearner with fast, local pattern matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Extracted Knowledge Types ──

@dataclass
class Fact:
    """A single extracted fact: subject-relation-object triple."""
    subject: str
    relation: str
    obj: str
    confidence: float = 0.8
    source: str = "nlu"

    def as_text(self) -> str:
        return f"{self.subject} {self.relation} {self.obj}"

    def as_triple(self) -> tuple[str, str, str]:
        return (self.subject, self.relation, self.obj)


@dataclass
class Entity:
    """A recognized entity in text."""
    text: str
    label: str  # person, place, number, date, concept
    start: int = 0
    end: int = 0


@dataclass
class Preference:
    """A user preference: subject likes/dislikes something."""
    subject: str  # usually "user"
    sentiment: str  # "likes", "dislikes", "prefers"
    target: str
    confidence: float = 0.9


@dataclass
class Correction:
    """A correction to existing knowledge."""
    original: str
    corrected: str
    confidence: float = 0.95


@dataclass
class NLUResult:
    """Complete NLU analysis of a text."""
    facts: list[Fact] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    preferences: list[Preference] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    intent: str = "statement"
    keywords: list[str] = field(default_factory=list)


# ── Extraction Patterns ──

# "X is Y" patterns (definition/classification)
_IS_PATTERNS = [
    re.compile(r"^(?:the\s+)?(\w[\w\s]*?)\s+is\s+(?:a|an|the)?\s*(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+(?:are|were)\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+means?\s+(.+?)\.?$", re.I),
]

# "The X of Y is Z" patterns (property extraction)
_PROPERTY_PATTERNS = [
    re.compile(r"^the\s+(\w+)\s+of\s+(\w[\w\s]*?)\s+is\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)(?:'s|'s)\s+(\w+)\s+is\s+(.+?)\.?$", re.I),
]

# "I have a X named Y" patterns (possession with name)
_POSSESSION_PATTERNS = [
    re.compile(r"^i\s+have\s+(?:a|an)\s+(\w+)\s+(?:named|called)\s+(.+?)\.?$", re.I),
    re.compile(r"^i\s+(?:own|got)\s+(?:a|an)\s+(\w+)\s+(?:named|called)\s+(.+?)\.?$", re.I),
    re.compile(r"^my\s+(\w+)(?:'s\s+name)?\s+is\s+(.+?)\.?$", re.I),
    re.compile(r"^my\s+(\w+)\s+is\s+(?:named|called)\s+(.+?)\.?$", re.I),
]

# "X has Y" / "X contains Y"
_HAS_PATTERNS = [
    re.compile(r"^(\w[\w\s]*?)\s+has\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+contains?\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+includes?\s+(.+?)\.?$", re.I),
]

# "X causes Y" / "X leads to Y"
_CAUSAL_PATTERNS = [
    re.compile(r"^(\w[\w\s]*?)\s+causes?\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+leads?\s+to\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+results?\s+in\s+(.+?)\.?$", re.I),
    re.compile(r"^if\s+(.+?),?\s+then\s+(.+?)\.?$", re.I),
]

# "X was created/invented/discovered by Y" (attribution)
_ATTRIBUTION_PATTERNS = [
    re.compile(r"^(\w[\w\s]*?)\s+was\s+(?:created|invented|discovered|founded|built|written|made)\s+(?:by|in)\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+(?:created|invented|discovered|founded|built|wrote)\s+(.+?)\.?$", re.I),
]

# "X can/does Y" (capability/action)
_ACTION_PATTERNS = [
    re.compile(r"^(\w[\w\s]*?)\s+can\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+(?:is\s+used\s+(?:for|to)|is\s+(?:for|about))\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+works\s+(?:by|with|in)\s+(.+?)\.?$", re.I),
]

# "X is located/found in Y" (location)
_LOCATION_PATTERNS = [
    re.compile(r"^(\w[\w\s]*?)\s+is\s+(?:located|found|situated)\s+in\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+lives?\s+in\s+(.+?)\.?$", re.I),
    re.compile(r"^i\s+(?:live|am|work)\s+in\s+(.+?)\.?$", re.I),
]

# Personal information: "My name is X", "I am X", "I work at X"
_PERSONAL_PATTERNS = [
    re.compile(r"^my\s+name\s+is\s+(.+?)\.?$", re.I),
    re.compile(r"^i\s+am\s+(?:a\s+)?(\w[\w\s]*?)\.?$", re.I),
    re.compile(r"^i\s+work\s+(?:at|in|for|as)\s+(.+?)\.?$", re.I),
    re.compile(r"^i'?m\s+(?:a\s+)?(\w[\w\s]*?)\.?$", re.I),
    re.compile(r"^call\s+me\s+(.+?)\.?$", re.I),
]

# "X is part of Y" / "X belongs to Y"
_PART_OF_PATTERNS = [
    re.compile(r"^(\w[\w\s]*?)\s+is\s+(?:a\s+)?part\s+of\s+(.+?)\.?$", re.I),
    re.compile(r"^(\w[\w\s]*?)\s+belongs?\s+to\s+(.+?)\.?$", re.I),
]

# Preference patterns
_LIKE_PATTERNS = [
    re.compile(r"^i\s+(?:really\s+)?(?:like|love|enjoy|prefer)\s+(.+?)\.?$", re.I),
    re.compile(r"^(?:my|i)\s+(?:favorite|favourite)\s+(?:\w+\s+)?is\s+(.+?)\.?$", re.I),
    re.compile(r"^i\s+(?:always|usually)\s+(?:use|go with|choose)\s+(.+?)\.?$", re.I),
]

_DISLIKE_PATTERNS = [
    re.compile(r"^i\s+(?:really\s+)?(?:hate|dislike|don'?t\s+like|can'?t\s+stand)\s+(.+?)\.?$", re.I),
    re.compile(r"^i\s+(?:never|rarely)\s+(?:use|go with|choose)\s+(.+?)\.?$", re.I),
]

# Correction patterns
_CORRECTION_PATTERNS = [
    re.compile(r"^(?:no|nope|wrong),?\s+(?:it'?s|it\s+is|that'?s|that\s+is)\s+(?:actually\s+)?(.+?)\.?$", re.I),
    re.compile(r"^actually,?\s+(.+?)\.?$", re.I),
    re.compile(r"^that'?s\s+(?:not\s+right|wrong|incorrect),?\s*(.+?)\.?$", re.I),
    re.compile(r"^correction:?\s+(.+?)\.?$", re.I),
]

# Teaching patterns: "remember that X", "X means Y"
_TEACH_PATTERNS = [
    re.compile(r"^remember\s+that\s+(.+?)\.?$", re.I),
    re.compile(r"^learn\s+(?:this|that):?\s+(.+?)\.?$", re.I),
    re.compile(r"^(?:did\s+you\s+know|fun\s+fact):?\s+(.+?)\.?$", re.I),
]

# Stop words for keyword extraction
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "i", "you", "he",
    "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "his", "its", "our", "their", "what", "which", "who", "whom",
    "this", "that", "these", "those", "am", "at", "by", "for", "with",
    "about", "between", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "in", "out", "on", "off", "over",
    "under", "again", "further", "then", "once", "here", "there", "when",
    "where", "why", "how", "all", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "don", "now", "of", "and", "or", "but",
    "if", "because", "as", "until", "while", "tell", "me", "please",
})


class NLUEngine:
    """Local natural language understanding — no LLM required.

    Extracts facts, entities, preferences, and corrections from text
    using pattern matching and heuristics.
    """

    def analyze(self, text: str) -> NLUResult:
        """Run full NLU analysis on input text."""
        result = NLUResult()
        text = text.strip()
        if not text:
            return result

        result.keywords = self._extract_keywords(text)
        result.entities = self._extract_entities(text)
        result.facts = self._extract_facts(text)
        result.preferences = self._extract_preferences(text)
        result.corrections = self._extract_corrections(text)
        result.intent = self._classify_intent(text)

        return result

    def extract_facts_from_conversation(
        self, user_input: str, jarvis_response: str
    ) -> list[Fact]:
        """Extract teachable facts from a conversation exchange.

        Looks at what the user said for:
        - Direct facts ("Python is a programming language")
        - Preferences ("I like dark mode")
        - Corrections ("No, it's actually 42")
        - Teaching ("Remember that my birthday is March 5")
        """
        facts = []

        # Check for explicit teaching first — extract the taught content
        taught = False
        for pattern in _TEACH_PATTERNS:
            m = pattern.match(user_input.strip())
            if m:
                content = m.group(1).strip()
                inner_facts = self._extract_facts(content)
                if inner_facts:
                    facts.extend(inner_facts)
                else:
                    facts.append(Fact(
                        subject="taught",
                        relation="is",
                        obj=content,
                        confidence=0.95,
                        source="user_taught",
                    ))
                taught = True
                break

        # Only extract facts from raw input if not a teaching pattern
        if not taught:
            facts.extend(self._extract_facts(user_input))

        # Extract preferences
        prefs = self._extract_preferences(user_input)
        for pref in prefs:
            facts.append(Fact(
                subject=pref.subject,
                relation=pref.sentiment,
                obj=pref.target,
                confidence=pref.confidence,
                source="preference",
            ))

        return facts

    # ── Fact Extraction ──

    def _extract_facts(self, text: str) -> list[Fact]:
        """Extract subject-relation-object triples from text."""
        facts = []
        sentences = self._split_sentences(text)

        for sentence in sentences:
            s = sentence.strip()
            if len(s) < 5:
                continue

            # Possession: "I have a dog named Rex"
            for pattern in _POSSESSION_PATTERNS:
                m = pattern.match(s)
                if m:
                    thing, name = m.group(1).strip(), m.group(2).strip()
                    facts.append(Fact(
                        subject="user",
                        relation=f"has {thing} named",
                        obj=name,
                        confidence=0.9,
                    ))
                    break
            else:
                pass  # Fall through to other patterns

            # Property patterns: "the X of Y is Z"
            for pattern in _PROPERTY_PATTERNS:
                m = pattern.match(s)
                if m:
                    prop, subj, val = m.group(1), m.group(2), m.group(3)
                    facts.append(Fact(
                        subject=subj.strip().lower(),
                        relation=f"has_property {prop.strip().lower()}",
                        obj=val.strip(),
                        confidence=0.85,
                    ))
                    break
            else:
                # "X is Y" patterns
                for pattern in _IS_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        subj, obj = m.group(1).strip(), m.group(2).strip()
                        if len(subj) > 1 and len(obj) > 1:
                            facts.append(Fact(
                                subject=subj.lower(),
                                relation="is",
                                obj=obj,
                                confidence=0.8,
                            ))
                        break

                # "X has Y"
                for pattern in _HAS_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        facts.append(Fact(
                            subject=m.group(1).strip().lower(),
                            relation="has",
                            obj=m.group(2).strip(),
                            confidence=0.75,
                        ))
                        break

                # "X causes Y"
                for pattern in _CAUSAL_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        facts.append(Fact(
                            subject=m.group(1).strip().lower(),
                            relation="causes",
                            obj=m.group(2).strip(),
                            confidence=0.7,
                        ))
                        break

                # "X is part of Y"
                for pattern in _PART_OF_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        facts.append(Fact(
                            subject=m.group(1).strip().lower(),
                            relation="part_of",
                            obj=m.group(2).strip(),
                            confidence=0.8,
                        ))
                        break

                # Attribution: "X was created by Y"
                for pattern in _ATTRIBUTION_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        facts.append(Fact(
                            subject=m.group(1).strip().lower(),
                            relation="created_by",
                            obj=m.group(2).strip(),
                            confidence=0.85,
                        ))
                        break

                # Action/capability: "X can Y", "X is used for Y"
                for pattern in _ACTION_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        facts.append(Fact(
                            subject=m.group(1).strip().lower(),
                            relation="can",
                            obj=m.group(2).strip(),
                            confidence=0.75,
                        ))
                        break

                # Location: "X is located in Y", "I live in Y"
                for pattern in _LOCATION_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        groups = m.groups()
                        if len(groups) == 2:
                            facts.append(Fact(
                                subject=groups[0].strip().lower(),
                                relation="located_in",
                                obj=groups[1].strip(),
                                confidence=0.8,
                            ))
                        elif len(groups) == 1:
                            facts.append(Fact(
                                subject="user",
                                relation="located_in",
                                obj=groups[0].strip(),
                                confidence=0.85,
                            ))
                        break

                # Personal: "My name is X", "I work at X"
                for pattern in _PERSONAL_PATTERNS:
                    m = pattern.match(s)
                    if m:
                        value = m.group(1).strip()
                        # Detect what kind of personal info
                        sl = s.lower()
                        if "name" in sl or "call me" in sl:
                            rel = "name is"
                        elif "work" in sl:
                            rel = "works in"
                        elif "am" in sl or "i'm" in sl:
                            rel = "is"
                        else:
                            rel = "is"
                        facts.append(Fact(
                            subject="user",
                            relation=rel,
                            obj=value,
                            confidence=0.9,
                            source="personal",
                        ))
                        break

        return facts

    # ── Entity Extraction ──

    def _extract_entities(self, text: str) -> list[Entity]:
        """Extract named entities using heuristics."""
        entities = []

        # Capitalized words (potential proper nouns) — skip sentence starts
        for m in re.finditer(r"(?<=[.!?\s])\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", text):
            entities.append(Entity(
                text=m.group(1), label="name",
                start=m.start(1), end=m.end(1),
            ))

        # Numbers
        for m in re.finditer(r"\b(\d+(?:\.\d+)?)\b", text):
            entities.append(Entity(
                text=m.group(1), label="number",
                start=m.start(1), end=m.end(1),
            ))

        # Dates (basic patterns)
        for m in re.finditer(
            r"\b(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|"
            r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}(?:,?\s+\d{4})?)\b",
            text, re.I,
        ):
            entities.append(Entity(
                text=m.group(0), label="date",
                start=m.start(), end=m.end(),
            ))

        # Email addresses
        for m in re.finditer(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", text):
            entities.append(Entity(
                text=m.group(0), label="email",
                start=m.start(), end=m.end(),
            ))

        return entities

    # ── Preference Extraction ──

    def _extract_preferences(self, text: str) -> list[Preference]:
        """Extract user preferences from text."""
        prefs = []

        for pattern in _LIKE_PATTERNS:
            m = pattern.match(text.strip())
            if m:
                prefs.append(Preference(
                    subject="user",
                    sentiment="likes",
                    target=m.group(1).strip(),
                ))

        for pattern in _DISLIKE_PATTERNS:
            m = pattern.match(text.strip())
            if m:
                prefs.append(Preference(
                    subject="user",
                    sentiment="dislikes",
                    target=m.group(1).strip(),
                ))

        return prefs

    # ── Correction Detection ──

    def _extract_corrections(self, text: str) -> list[Correction]:
        """Detect when the user is correcting Jarvis."""
        corrections = []

        for pattern in _CORRECTION_PATTERNS:
            m = pattern.match(text.strip())
            if m:
                corrections.append(Correction(
                    original="",  # Previous answer unknown at extraction time
                    corrected=m.group(1).strip(),
                ))
                break

        return corrections

    # ── Intent Classification ──

    def _classify_intent(self, text: str) -> str:
        """Classify the intent of the input."""
        q = text.lower().strip()

        if any(q.startswith(w) for w in ["what", "who", "where", "when", "why", "how", "is ", "can ", "does "]):
            return "question"
        if q.endswith("?"):
            return "question"
        if any(w in q for w in ["remember that", "learn this", "did you know"]):
            return "teach"
        if any(w in q for w in ["no,", "wrong", "actually", "correction"]):
            return "correction"
        if any(w in q for w in ["i like", "i love", "i hate", "i prefer", "my favorite"]):
            return "preference"

        # Check for facts (statements with "is", "has", "means")
        if any(w in q for w in [" is ", " are ", " has ", " means ", " causes "]):
            return "statement"

        return "other"

    # ── Keyword Extraction ──

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text."""
        words = re.findall(r"[a-zA-Z]+", text.lower())
        return [w for w in words if w not in _STOP_WORDS and len(w) > 2]

    # ── Helpers ──

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences."""
        return re.split(r"(?<=[.!?])\s+", text.strip())


# ── Knowledge Gap Detection ──

class GapDetector:
    """Detects knowledge gaps without LLM — uses keyword coverage analysis.

    Compares user query keywords against known knowledge domains
    to identify what Jarvis doesn't know but should.
    """

    def __init__(self):
        self._domain_keywords: dict[str, set[str]] = {}

    def register_domain(self, domain: str, keywords: set[str]):
        """Register a knowledge domain with its keywords."""
        self._domain_keywords[domain] = keywords

    def detect_gaps(
        self,
        user_query: str,
        known_facts: list[str],
    ) -> list[dict]:
        """Find knowledge gaps by comparing query to known facts.

        Returns a list of gap dicts with 'topic', 'question', 'importance'.
        """
        nlu = NLUEngine()
        query_keywords = set(nlu._extract_keywords(user_query))

        # Get keywords from all known facts
        known_keywords: set[str] = set()
        for fact in known_facts:
            known_keywords.update(nlu._extract_keywords(fact))

        # Find query keywords not covered by known facts
        unknown_keywords = query_keywords - known_keywords - _STOP_WORDS

        gaps = []
        for keyword in unknown_keywords:
            if len(keyword) > 3:  # Skip very short words
                gaps.append({
                    "topic": keyword,
                    "question": f"Can you tell me about {keyword}?",
                    "importance": "medium",
                })

        return gaps[:3]  # Max 3 gaps


# ── Question Generation ──

class QuestionGenerator:
    """Generates curiosity questions from knowledge gaps — no LLM needed."""

    _TEMPLATES = [
        "I know about {known} but not about {unknown} — can you tell me about {unknown}?",
        "You mentioned {unknown} — what does that mean?",
        "How does {unknown} relate to {known}?",
        "I'd like to learn more about {unknown}. Can you explain?",
    ]

    def generate(
        self,
        unknown_topic: str,
        known_topics: list[str] | None = None,
    ) -> str:
        """Generate a natural curiosity question."""
        import random

        if known_topics:
            known = random.choice(known_topics)
            template = random.choice(self._TEMPLATES)
            return template.format(known=known, unknown=unknown_topic)
        else:
            return f"Can you tell me about {unknown_topic}?"
