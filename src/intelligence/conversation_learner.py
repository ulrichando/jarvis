"""JARVIS Conversation Learner — extract knowledge from conversations.

Instead of memorizing every sentence, JARVIS extracts:
- Facts Ulrich mentions ("I work at Google", "My birthday is March 5")
- Preferences ("I like Python", "I hate JavaScript")
- Corrections ("No, it's actually 42", "That's wrong, it's...")
- Skills/procedures Ulrich teaches ("When I say X, do Y")
- Important context ("The project deadline is Friday")

This runs AFTER every response, analyzing the conversation for learnable moments.
"""

import re
from src.reasoning.groq_client import GroqReasoner
from src.memory.store import MemoryStore
from src.memory.lattice.node import NodeType


EXTRACT_PROMPT = """Analyze this conversation. Extract useful knowledge about Ulrich and how JARVIS should behave.

Extract:
- Personal facts: "Ulrich works in cybersecurity", "Ulrich is from [country]"
- Preferences: "Ulrich prefers Python", "Ulrich likes dark mode"
- Habits: "Ulrich usually works late", "Ulrich scans networks often"
- Corrections: if JARVIS was wrong and Ulrich corrected it, note the correction
- Instructions: "When Ulrich says X, do Y"
- Opinions: "Ulrich thinks React is overrated"
- Projects: "Ulrich is working on JARVIS AI project"
- Relationships: "Ulrich mentioned a colleague named [name]"
- Inside jokes: if something funny happened, note it briefly
- Skills: "Ulrich knows Rust", "Ulrich is learning Go"

Do NOT extract:
- Generic questions ("what time is it")
- JARVIS's own responses
- Greetings or filler

One fact per line. Under 15 words each. NONE if nothing useful."""


class ConversationLearner:
    """Learns useful knowledge from conversations."""

    def __init__(self, reasoner: GroqReasoner, memory: MemoryStore):
        self.reasoner = reasoner
        self.memory = memory
        self._buffer: list[tuple[str, str]] = []  # (role, content) pairs
        self._learn_every = 3  # Analyze every N exchanges — learn fast
        self._exchange_count = 0

    async def observe(self, user_input: str, jarvis_response: str):
        """Observe a conversation exchange. Periodically extract knowledge."""
        self._buffer.append(("user", user_input))
        self._buffer.append(("jarvis", jarvis_response))
        self._exchange_count += 1

        # Only analyze periodically — not every single message
        if self._exchange_count >= self._learn_every:
            await self._extract_and_learn()
            self._exchange_count = 0

    async def _extract_and_learn(self):
        """Analyze buffered conversation and extract facts."""
        if not self._buffer:
            return

        # Build conversation text
        convo = "\n".join(f"{role}: {content[:200]}" for role, content in self._buffer[-10:])
        self._buffer = self._buffer[-4:]  # Keep only recent for next batch

        try:
            response = await self.reasoner.query(
                f"Conversation:\n{convo}",
                system_prompt=EXTRACT_PROMPT,
                history=None,
            )

            response = response.strip()
            if response.upper() == "NONE" or len(response) < 5:
                return

            # Store each extracted fact
            facts = [line.strip() for line in response.split("\n") if line.strip() and len(line.strip()) > 5]
            for fact in facts[:5]:  # Max 5 facts per extraction
                # Determine type
                fact_lower = fact.lower()
                if any(w in fact_lower for w in ["prefer", "like", "hate", "favorite", "always"]):
                    node_type = NodeType.SKILL  # Preference/behavioral
                elif any(w in fact_lower for w in ["when", "if", "should", "do this"]):
                    node_type = NodeType.SKILL  # Instruction
                else:
                    node_type = NodeType.FACT

                self.memory.learn(fact, node_type, ["conversation", "auto-learned"])

        except Exception:
            pass  # Don't crash the brain if learning fails

    async def force_learn(self):
        """Force immediate learning from buffer."""
        if self._buffer:
            await self._extract_and_learn()
