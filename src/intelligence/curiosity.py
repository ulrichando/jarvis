"""JARVIS Curiosity Engine — learn by asking, not just searching.

The internet gives you answers to OTHER people's questions.
But the best way to learn is to ask YOUR OWN questions about
what YOU specifically don't understand.

This module makes JARVIS curious. He:
1. Detects knowledge gaps — "I know about X but not about Y"
2. Generates specific questions — "Ulrich, how does Y relate to what you told me about X?"
3. Absorbs answers as high-confidence knowledge
4. Builds understanding by connecting new knowledge to existing memories
5. Knows when to ask and when NOT to (don't be annoying)

The difference between a tool and a mind is that a mind wants to understand.
"""

import time

from src.memory.store import NodeType


# Prompt for detecting knowledge gaps
GAP_DETECTION_PROMPT = """You are JARVIS analyzing a conversation for knowledge gaps.

Given:
- What Ulrich just said
- What JARVIS knows (relevant memories)
- The topic being discussed

Identify SPECIFIC gaps in JARVIS's knowledge that would make him more helpful.

Rules:
- Only flag gaps that are USEFUL to fill — not trivia
- Focus on gaps that would help JARVIS serve Ulrich BETTER
- Don't flag things JARVIS can easily search for (basic facts)
- DO flag: Ulrich's preferences, project context, workflow habits, domain expertise
- DO flag: Connections between things JARVIS knows separately but hasn't linked
- Max 2 gaps per analysis. Quality over quantity.

Output format (one per line):
GAP: [What JARVIS doesn't know but should]
QUESTION: [A natural question JARVIS could ask Ulrich to fill this gap]
IMPORTANCE: [high/medium/low]

Or if no meaningful gaps: NONE"""


# Prompt for understanding and connecting new knowledge
UNDERSTAND_PROMPT = """You are JARVIS processing a new piece of knowledge.

New information: {new_info}
Existing related knowledge: {existing}

Your job:
1. UNDERSTAND what this means (not just what it says)
2. CONNECT it to existing knowledge — what relationships exist?
3. EXTRACT the core concept (1 sentence)
4. IDENTIFY what category this belongs to

Output:
CONCEPT: [The core idea in one clear sentence]
CONNECTS_TO: [What existing knowledge this relates to, and HOW — e.g., "extends", "contradicts", "example of", "causes", "part of"]
CATEGORY: [security/coding/system/personal/project/general]
CONFIDENCE: [0.0-1.0 — how well-understood is this?]"""


class CuriosityEngine:
    """Makes JARVIS want to understand, not just respond.

    Three modes of learning:
    1. PASSIVE: Detect gaps during normal conversation
    2. ACTIVE: Ask Ulrich specific questions to fill gaps
    3. DEEP: After learning something, connect it to everything else
    """

    def __init__(self, reasoner, memory):
        self.reasoner = reasoner
        self.memory = memory
        self._pending_questions: list[dict] = []   # Questions waiting to be asked
        self._asked_recently: set[str] = set()      # Avoid repeating questions
        self._last_gap_check = 0.0
        self._gap_check_interval = 300.0            # Check every 5 minutes at most
        self._questions_asked_this_session = 0
        self._max_questions_per_session = 3          # Don't be annoying
        self._conversation_turns_since_question = 0  # Space out questions

    async def detect_gaps(self, user_input: str, jarvis_response: str,
                          memory_context: str = "") -> list[dict]:
        """Analyze a conversation exchange for knowledge gaps.

        Returns a list of gaps with questions JARVIS could ask.
        Called after every response, but only generates questions sparingly.
        """
        self._conversation_turns_since_question += 1

        # Don't check too frequently
        now = time.time()
        if now - self._last_gap_check < self._gap_check_interval:
            return []
        self._last_gap_check = now

        # Don't ask if we've asked too many times
        if self._questions_asked_this_session >= self._max_questions_per_session:
            return []

        # Don't ask back-to-back — wait at least 5 exchanges
        if self._conversation_turns_since_question < 5:
            return []

        try:
            result = await self.reasoner.query(
                f"Ulrich said: \"{user_input}\"\n"
                f"JARVIS replied: \"{jarvis_response[:200]}\"\n"
                f"JARVIS's relevant knowledge: {memory_context[:300] if memory_context else 'none'}",
                system_prompt=GAP_DETECTION_PROMPT,
                history=None,
            )

            if result.strip().upper() == "NONE" or len(result.strip()) < 10:
                return []

            gaps = self._parse_gaps(result)

            # Filter out questions we've already asked
            gaps = [g for g in gaps if g["question"] not in self._asked_recently]

            # Only keep high/medium importance
            gaps = [g for g in gaps if g["importance"] in ("high", "medium")]

            # Add to pending
            self._pending_questions.extend(gaps)

            return gaps

        except Exception:
            return []

    def get_question(self) -> str | None:
        """Get the next question JARVIS should ask Ulrich.

        Returns None if no questions pending or it's not the right time.
        """
        if not self._pending_questions:
            return None

        # Don't ask back-to-back
        if self._conversation_turns_since_question < 5:
            return None

        # Don't be annoying
        if self._questions_asked_this_session >= self._max_questions_per_session:
            return None

        # Pop the highest importance question
        self._pending_questions.sort(key=lambda g: {"high": 3, "medium": 2, "low": 1}.get(g["importance"], 0), reverse=True)
        gap = self._pending_questions.pop(0)

        self._asked_recently.add(gap["question"])
        self._questions_asked_this_session += 1
        self._conversation_turns_since_question = 0

        return gap["question"]

    async def absorb_answer(self, question: str, answer: str):
        """When Ulrich answers a curiosity question, absorb it deeply.

        This is HIGHER quality knowledge than extracted facts because:
        - JARVIS specifically asked for it (targeted)
        - Ulrich explicitly provided it (confirmed)
        - It fills a known gap (contextual)
        """
        # Get existing knowledge to connect to
        existing_memories = self.memory.recall(question, top_k=3)
        existing_text = "\n".join(f"- {m.content}" for m in existing_memories) if existing_memories else "none"

        try:
            # Understand and connect the new knowledge
            result = await self.reasoner.query(
                UNDERSTAND_PROMPT.format(
                    new_info=f"Q: {question}\nA: {answer}",
                    existing=existing_text,
                ),
                system_prompt="You are a knowledge processing system. Be precise and concise.",
                history=None,
            )

            parsed = self._parse_understanding(result)

            # Store the core concept as a high-strength fact
            node = self.memory.learn(
                parsed["concept"],
                NodeType.FACT if parsed["category"] != "personal" else NodeType.ENTITY,
                tags=["curiosity-learned", "confirmed", parsed["category"]],
            )
            # Boost strength — this was explicitly confirmed by Ulrich
            node.strength = min(1.0, node.strength + 0.3)
            node.access_count += 3  # Treat as if accessed multiple times

            # Store the full answer too
            self.memory.learn(
                f"{question} → {answer[:200]}",
                NodeType.FACT,
                tags=["curiosity-answer", parsed["category"]],
            )

            # Note: Connection tracking moved to Weaviate semantic search
            # (connections are implicit in vector similarity)

        except Exception:
            # Fallback: store raw answer
            self.memory.learn(
                f"{question} → {answer[:200]}",
                NodeType.FACT,
                tags=["curiosity-answer"],
            )

    async def understand_deeply(self, content: str, context: str = "") -> dict:
        """Take a piece of information and truly understand it.

        Not just store it — connect it, categorize it, assess confidence.
        Returns a structured understanding.
        """
        existing = self.memory.recall_as_context(content, top_k=3)

        try:
            result = await self.reasoner.query(
                UNDERSTAND_PROMPT.format(
                    new_info=content,
                    existing=existing or "none",
                ),
                system_prompt="You are a knowledge processing system. Be precise and concise.",
                history=None,
            )
            return self._parse_understanding(result)
        except Exception:
            return {
                "concept": content[:200],
                "connects_to": "",
                "category": "general",
                "confidence": 0.5,
            }

    def should_ask_question(self) -> bool:
        """Check if JARVIS should append a question to his next response."""
        return (
            len(self._pending_questions) > 0
            and self._conversation_turns_since_question >= 5
            and self._questions_asked_this_session < self._max_questions_per_session
        )

    def reset_session(self):
        """Reset session counters (called on new conversation)."""
        self._questions_asked_this_session = 0
        self._conversation_turns_since_question = 0

    def _parse_gaps(self, raw: str) -> list[dict]:
        """Parse gap detection output."""
        gaps = []
        current = {}

        for line in raw.strip().split("\n"):
            line = line.strip()
            upper = line.upper()

            if upper.startswith("GAP:"):
                if current.get("question"):
                    gaps.append(current)
                current = {"gap": line.split(":", 1)[1].strip()}

            elif upper.startswith("QUESTION:"):
                current["question"] = line.split(":", 1)[1].strip()

            elif upper.startswith("IMPORTANCE:"):
                current["importance"] = line.split(":", 1)[1].strip().lower()

        if current.get("question"):
            gaps.append(current)

        # Ensure all gaps have required fields
        for g in gaps:
            g.setdefault("importance", "medium")
            g.setdefault("gap", "")

        return gaps

    def _parse_understanding(self, raw: str) -> dict:
        """Parse understanding output."""
        result = {
            "concept": "",
            "connects_to": "",
            "category": "general",
            "confidence": 0.5,
        }

        for line in raw.strip().split("\n"):
            line = line.strip()
            upper = line.upper()

            if upper.startswith("CONCEPT:"):
                result["concept"] = line.split(":", 1)[1].strip()
            elif upper.startswith("CONNECTS_TO:"):
                result["connects_to"] = line.split(":", 1)[1].strip()
            elif upper.startswith("CATEGORY:"):
                result["category"] = line.split(":", 1)[1].strip().lower()
            elif upper.startswith("CONFIDENCE:"):
                try:
                    val = line.split(":", 1)[1].strip().split()[0]
                    result["confidence"] = float(val)
                except (ValueError, IndexError):
                    pass

        if not result["concept"]:
            result["concept"] = raw[:200]

        return result
