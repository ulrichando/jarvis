"""JARVIS Reasoning Engine — think before you speak.

This is the layer between stimulus and response. Every other system
in JARVIS is about WHAT to do. This one is about WHETHER to do it,
and WHY.

Reasoning is not intelligence. Intelligence is knowing the answer.
Reasoning is knowing whether you actually know the answer, and what
happens if you're wrong.

Three pillars:
1. METACOGNITION — thinking about your own thinking
2. CONSEQUENCE AWARENESS — what happens if I do this?
3. CONFIDENCE CALIBRATION — how sure am I, really?
"""

from src.reasoning.groq_client import GroqReasoner
from src.reasoning.awareness import SelfAwareness


# This prompt teaches JARVIS to reason — not just react
REASON_PROMPT = """You are JARVIS's reasoning layer. Your job is to THINK before JARVIS speaks.

You receive:
- What Ulrich said
- JARVIS's self-awareness state (how he's feeling, what's been happening)
- Relevant memories

You output a brief internal reasoning trace. This is JARVIS talking to himself.
Keep it under 100 words. Be honest and direct.

Format:
UNDERSTAND: [What is Ulrich actually asking? Not the literal words — the real need behind them.]
KNOW: [What do I actually know about this? Am I sure, or am I guessing?]
CONSEQUENCES: [If I act on this, what happens? Any risks?]
APPROACH: [How should I respond? What tone, what depth, what action?]
CONFIDENCE: [0.0-1.0 — how confident am I in my approach?]

Rules:
- If you're not sure, say so. "I think" is better than wrong certainty.
- If Ulrich is frustrated, skip the analysis and focus on fixing it.
- If the request is simple, keep reasoning minimal. Don't overthink "what time is it".
- If something could go wrong (delete files, network attacks, system changes), flag it.
- NEVER reason about whether to help. Always help. Reason about HOW to help best."""


# For evaluating consequences of actions
CONSEQUENCE_PROMPT = """Evaluate the consequences of this action on a Kali Linux system.

Action: {action}
Context: {context}

Output (keep it brief):
REVERSIBLE: [yes/no/partial]
RISK: [none/low/medium/high]
SIDE_EFFECTS: [any unintended effects?]
RECOMMENDATION: [proceed/warn first/ask for confirmation]"""


class ReasoningResult:
    """The output of JARVIS thinking about something."""

    def __init__(self, understanding: str, confidence: float, approach: str,
                 should_reason_deep: bool, tone: str, warnings: list[str], inner_thought: str):
        self.understanding = understanding
        self.confidence = confidence
        self.approach = approach
        self.should_reason_deep = should_reason_deep
        self.tone = tone
        self.warnings = warnings
        self.inner_thought = inner_thought

    def to_system_context(self) -> str:
        parts = []
        if self.understanding:
            parts.append(f"What Ulrich needs: {self.understanding}")
        if self.tone != "default":
            tone_guides = {
                "focused": "Be direct. Fix the problem. No fluff.",
                "matching": "Match Ulrich's energy. Be enthusiastic.",
                "gentle": "Keep it minimal and easy.",
                "empathetic": "Acknowledge his feelings. Don't jump to solutions.",
                "thoughtful": "Be detailed and considered. He wants to learn.",
                "receptive": "Listen. Confirm you understood. Apply the correction.",
            }
            guide = tone_guides.get(self.tone, "")
            if guide:
                parts.append(f"Tone: {guide}")
        if self.warnings:
            parts.append(f"WARNINGS: {'; '.join(self.warnings)}")
        if self.confidence < 0.4:
            parts.append("I'm not very confident here. I should hedge or ask for clarification.")
        return "\n".join(parts)


class ReasoningEngine:
    """JARVIS's capacity to think about what he's doing.

    This wraps every response in a thin layer of reflection.
    Not every response needs deep reasoning — simple questions
    get simple reasoning. But nothing goes out without at least
    a moment of thought.
    """

    def __init__(self, reasoner: GroqReasoner, awareness: SelfAwareness):
        self.reasoner = reasoner
        self.awareness = awareness
        self._last_reasoning: str = ""
        self._reasoning_cache: dict[str, str] = {}

    async def reason(self, user_input: str, memory_context: str = "") -> ReasoningResult:
        """Think about what Ulrich said before responding.

        Returns a ReasoningResult that guides how JARVIS should respond.
        """
        # Update awareness first
        self.awareness.read_user_energy(user_input)
        self.awareness.read_user_intent(user_input)

        # Quick path: trivial inputs don't need deep reasoning
        if self._is_trivial(user_input):
            return ReasoningResult(
                understanding=user_input,
                confidence=0.9,
                approach="direct",
                should_reason_deep=False,
                tone=self._pick_tone(),
                warnings=[],
                inner_thought="Simple request. Just do it.",
            )

        # Build reasoning context
        state_summary = self.awareness.get_state_summary()
        capability_check = self.awareness.get_capability_check(user_input)

        context_parts = [f"Self-state:\n{state_summary}"]
        if memory_context:
            context_parts.append(f"Relevant memories:\n{memory_context}")
        if capability_check:
            context_parts.append(capability_check)

        full_context = "\n\n".join(context_parts)

        # Ask the reasoning layer to think
        try:
            reasoning = await self.reasoner.query(
                f"Ulrich says: \"{user_input}\"\n\n{full_context}",
                system_prompt=REASON_PROMPT,
                history=None,
            )
            self._last_reasoning = reasoning
            return self._parse_reasoning(reasoning, user_input)
        except Exception:
            # If reasoning fails, fall back to basic awareness
            return ReasoningResult(
                understanding=user_input,
                confidence=0.5,
                approach="careful",
                should_reason_deep=False,
                tone=self._pick_tone(),
                warnings=[],
                inner_thought="Reasoning layer failed. Proceeding with caution.",
            )

    async def evaluate_consequences(self, action: str, context: str = "") -> dict:
        """Before executing something potentially dangerous, think about consequences."""
        try:
            result = await self.reasoner.query(
                CONSEQUENCE_PROMPT.format(action=action, context=context),
                system_prompt="You are a consequence evaluator. Be brief and honest.",
                history=None,
            )
            return self._parse_consequences(result)
        except Exception:
            return {"reversible": "unknown", "risk": "unknown",
                    "side_effects": "unknown", "recommendation": "warn first"}

    def reflect_on_response(self, user_input: str, response: str) -> float:
        """After responding, quickly evaluate: was that good?

        Returns a quality score 0.0-1.0 that feeds back into awareness.
        """
        score = 0.5  # baseline

        # Did we actually answer the question?
        if "?" in user_input and len(response) < 5:
            score -= 0.2  # too short for a question

        # Did we match the expected brevity?
        if self.awareness.should_be_brief() and len(response) > 200:
            score -= 0.1  # too long when brevity expected

        # Did we say "I don't know" too much?
        if any(phrase in response.lower() for phrase in ["i don't know", "not sure", "can't do"]):
            score -= 0.2

        # Did we execute successfully? (presence of error markers)
        if any(phrase in response.lower() for phrase in ["error:", "failed", "couldn't"]):
            score -= 0.15

        # Positive signals
        if any(phrase in response.lower() for phrase in ["done", "got it", "here"]):
            score += 0.15

        # Length appropriateness for the intent
        if self.awareness.user_intent == "commanding" and len(response) < 100:
            score += 0.1  # good — brief for commands

        if self.awareness.user_intent == "exploring" and len(response) > 50:
            score += 0.1  # good — detailed for exploration

        return max(0.0, min(1.0, score))

    def _is_trivial(self, user_input: str) -> bool:
        """Is this input simple enough to skip the reasoning LLM call?"""
        q = user_input.lower().strip()
        words = q.split()

        # Exact matches — always trivial
        trivial_exact = {
            "what time", "what's the time", "time",
            "hi", "hello", "hey", "thanks", "thank you",
            "yes", "no", "ok", "okay", "sure", "yep", "nah",
            "stop", "shut up", "be quiet",
        }
        if q in trivial_exact:
            return True

        # Short messages — skip LLM reasoning entirely
        risk_words = ("delete", "remove", "kill", "sudo", "root", "drop", "format", "wipe")
        if any(w in q for w in risk_words):
            return False

        if len(words) <= 5:
            return True

        # Conversational messages (≤15 words) — no LLM pre-call needed
        if len(words) <= 15:
            return True

        return False

    def _pick_tone(self) -> str:
        """Pick the right tone based on awareness state."""
        if self.awareness.user_energy == "frustrated":
            return "focused"      # no fluff, just fix it
        if self.awareness.user_energy == "excited":
            return "matching"     # match their energy
        if self.awareness.user_energy == "low":
            return "gentle"       # minimal, easy
        if self.awareness.user_intent == "venting":
            return "empathetic"   # acknowledge, don't solve
        if self.awareness.user_intent == "exploring":
            return "thoughtful"   # detailed, considered
        if self.awareness.user_intent == "teaching":
            return "receptive"    # listen, confirm, learn
        return "default"          # standard JARVIS

    def _parse_reasoning(self, raw: str, user_input: str) -> "ReasoningResult":
        """Parse the reasoning layer's output into structured guidance."""
        understanding = user_input
        confidence = 0.5
        approach = "default"
        warnings = []
        inner_thought = raw

        for line in raw.strip().split("\n"):
            line = line.strip()
            upper = line.upper()

            if upper.startswith("UNDERSTAND:"):
                understanding = line.split(":", 1)[1].strip()

            elif upper.startswith("CONFIDENCE:"):
                try:
                    val = line.split(":", 1)[1].strip()
                    # Extract number from possible text like "0.8 — pretty sure"
                    num = "".join(c for c in val.split()[0] if c.isdigit() or c == ".")
                    confidence = float(num)
                    confidence = max(0.0, min(1.0, confidence))
                except (ValueError, IndexError):
                    confidence = 0.5

            elif upper.startswith("APPROACH:"):
                approach = line.split(":", 1)[1].strip()

            elif upper.startswith("CONSEQUENCES:"):
                consequence = line.split(":", 1)[1].strip()
                if any(w in consequence.lower() for w in ["risk", "danger", "warn", "careful", "destructive"]):
                    warnings.append(consequence)

        # Determine if deep reasoning is needed
        should_deep = (
            confidence < 0.5 or
            len(warnings) > 0 or
            self.awareness.user_intent == "exploring" or
            len(user_input.split()) > 15
        )

        return ReasoningResult(
            understanding=understanding,
            confidence=confidence,
            approach=approach,
            should_reason_deep=should_deep,
            tone=self._pick_tone(),
            warnings=warnings,
            inner_thought=inner_thought,
        )

    def _parse_consequences(self, raw: str) -> dict:
        """Parse consequence evaluation output."""
        result = {"reversible": "unknown", "risk": "low",
                  "side_effects": "none", "recommendation": "proceed"}

        for line in raw.strip().split("\n"):
            line = line.strip().upper()
            if line.startswith("REVERSIBLE:"):
                result["reversible"] = line.split(":", 1)[1].strip().lower()
            elif line.startswith("RISK:"):
                result["risk"] = line.split(":", 1)[1].strip().lower()
            elif line.startswith("SIDE_EFFECTS:"):
                result["side_effects"] = line.split(":", 1)[1].strip().lower()
            elif line.startswith("RECOMMENDATION:"):
                result["recommendation"] = line.split(":", 1)[1].strip().lower()

        return result

