"""JARVIS Local Conversation Learner — learns from what he hears and reads.

Replaces the LLM-dependent ConversationLearner with local NLU processing.
Instead of sending conversations to an LLM for fact extraction, uses
pattern-based NLU to extract structured knowledge.

The key difference from just storing raw text:
- Raw: "Python was created by Guido van Rossum in 1991"
  → stores the entire sentence as-is (parrot mode)

- Structured: extracts multiple facts:
  → (python, created_by, Guido van Rossum)
  → (python, created_in, 1991)
  → (Guido van Rossum, is, creator of Python)
  Then Jarvis can ANSWER QUESTIONS about Python's creator
  even if the question is phrased differently from the source.
"""

from __future__ import annotations

from brain.intelligence.nlu import NLUEngine, Fact, NLUResult


class LocalConversationLearner:
    """Learns structured knowledge from conversations — no LLM needed.

    Three learning modes:
    1. EXTRACT: Pull facts, preferences, corrections from user input
    2. CONNECT: Link new knowledge to existing knowledge graph
    3. REINFORCE: Strengthen facts that come up repeatedly
    """

    def __init__(self):
        self.nlu = NLUEngine()
        self._exchange_count = 0
        self._buffer: list[tuple[str, str]] = []
        self._learn_every = 1  # Learn from every exchange (fast local processing)

    def observe(self, user_input: str, jarvis_response: str) -> list[Fact]:
        """Observe a conversation exchange and extract learnable knowledge.

        Returns a list of structured facts to store in the knowledge graph.
        """
        self._buffer.append((user_input, jarvis_response))
        self._exchange_count += 1

        # Extract facts from user input
        facts = self.nlu.extract_facts_from_conversation(user_input, jarvis_response)

        return facts

    def process_ambient_speech(self, transcription: str) -> list[Fact]:
        """Process overheard speech (TV, conversations, podcasts).

        Instead of storing raw transcription, extracts structured facts
        that Jarvis can reason about later.

        This is the difference between "repeating what he heard" and
        "learning from what he heard".
        """
        if not transcription or len(transcription.strip()) < 10:
            return []

        # Split into sentences and analyze each
        result = self.nlu.analyze(transcription)
        facts = result.facts

        # Also check for facts in individual sentences
        sentences = self.nlu._split_sentences(transcription)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue
            sentence_facts = self.nlu._extract_facts(sentence)
            for f in sentence_facts:
                if f not in facts:
                    facts.append(f)

        # Tag ambient facts with lower confidence (not directly told to us)
        for fact in facts:
            fact.confidence *= 0.7  # Overheard knowledge is less reliable
            fact.source = "ambient"

        return facts

    def process_correction(
        self,
        user_input: str,
        previous_response: str,
    ) -> list[Fact]:
        """Process a user correction and generate replacement facts.

        When the user says "no, actually X is Y", we need to:
        1. Identify what the old fact was
        2. Create the corrected fact
        3. Mark the old fact for weakening/removal
        """
        result = self.nlu.analyze(user_input)

        corrected_facts = []
        for correction in result.corrections:
            # Try to extract facts from the corrected content
            inner_facts = self.nlu._extract_facts(correction.corrected)
            if inner_facts:
                for f in inner_facts:
                    f.confidence = 0.95  # User-confirmed corrections are high confidence
                    f.source = "correction"
                corrected_facts.extend(inner_facts)
            else:
                # Store the correction as a raw fact
                corrected_facts.append(Fact(
                    subject="corrected",
                    relation="is",
                    obj=correction.corrected,
                    confidence=0.95,
                    source="correction",
                ))

        return corrected_facts

    def extract_preferences(self, text: str) -> list[Fact]:
        """Extract user preferences from text.

        "I like Python" → (user, likes, Python)
        "I prefer dark mode" → (user, prefers, dark mode)
        """
        result = self.nlu.analyze(text)

        facts = []
        for pref in result.preferences:
            facts.append(Fact(
                subject=pref.subject,
                relation=pref.sentiment,
                obj=pref.target,
                confidence=pref.confidence,
                source="preference",
            ))

        return facts
