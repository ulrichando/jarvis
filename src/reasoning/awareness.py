"""JARVIS Self-Awareness Engine — know thyself.

This is JARVIS's inner mirror. It tracks:
- What state am I in right now? (mode, mood, confidence, load)
- What just happened? (recent actions, outcomes, errors)
- What can I do? What can't I do? (capability boundaries)
- What does Ulrich seem to need right now? (context reading)
- Am I being helpful or annoying? (self-evaluation)

Awareness isn't intelligence — it's the foundation intelligence stands on.
A system that doesn't know itself can't improve itself.
"""

import time
from dataclasses import dataclass, field
from collections import deque


@dataclass
class ActionTrace:
    """A record of something JARVIS did."""
    action: str          # what happened: "answered", "ran_command", "searched", "failed"
    detail: str          # specifics
    outcome: str         # "success", "failure", "partial", "unknown"
    confidence: float    # 0.0 to 1.0 — how sure was JARVIS about this
    timestamp: float = field(default_factory=time.time)


class SelfAwareness:
    """JARVIS's awareness of himself and his situation.

    This is not about being sentient. It's about being useful.
    A tool that knows its own limits is infinitely more valuable
    than one that doesn't.
    """

    def __init__(self):
        # --- Internal state ---
        self.mode = "normal"
        self.confidence = 0.5          # rolling average confidence
        self.energy = 1.0              # 1.0 = fresh, decays with errors/confusion
        self.consecutive_successes = 0
        self.consecutive_failures = 0
        self.interaction_count = 0

        # --- Recent history (short-term awareness) ---
        self.recent_actions: deque[ActionTrace] = deque(maxlen=20)
        self.recent_topics: deque[str] = deque(maxlen=10)
        self.recent_errors: deque[str] = deque(maxlen=5)

        # --- User state (what JARVIS perceives about Ulrich) ---
        self.user_energy = "neutral"    # "high", "neutral", "low", "frustrated", "excited"
        self.user_intent = "unknown"    # "asking", "commanding", "exploring", "venting", "teaching"
        self.conversation_depth = 0     # how deep into a topic we are

        # --- Vision context (set by web server when camera is active) ---
        self.vision_context = ""  # e.g. "1 person visible; smiling; well lit"

        # --- Capability awareness ---
        self.known_strengths = [
            "system commands", "file operations", "internet search",
            "code generation", "security tools", "conversation",
            "learning new skills", "autonomous execution",
        ]
        self.known_limits = [
            "cannot see the screen directly",
            "voice recognition may mishear",
            "internet search depends on connection",
            "complex math needs verification",
            "cannot undo destructive commands",
            "Groq API has rate limits and token caps",
            "local model is weaker than cloud model",
        ]

    def record_action(self, action: str, detail: str, outcome: str, confidence: float):
        """Record something JARVIS just did."""
        trace = ActionTrace(action, detail, outcome, confidence)
        self.recent_actions.append(trace)
        self.interaction_count += 1

        if outcome == "success":
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            self.energy = min(1.0, self.energy + 0.05)
        elif outcome == "failure":
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            self.energy = max(0.2, self.energy - 0.1)
            self.recent_errors.append(f"{action}: {detail}")

        # Rolling confidence
        self.confidence = (self.confidence * 0.7) + (confidence * 0.3)

    def read_user_energy(self, user_input: str):
        """Read Ulrich's energy from his words.

        Not sentiment analysis — it's pattern recognition.
        Short + aggressive = frustrated. Long + questioning = exploring.
        """
        q = user_input.lower().strip()
        words = q.split()
        word_count = len(words)

        # Frustration signals
        if any(w in q for w in ["wtf", "broken", "doesn't work", "still not",
                                 "again", "wrong", "fuck", "shit", "damn"]):
            self.user_energy = "frustrated"
            return

        # Excitement signals
        if any(w in q for w in ["awesome", "perfect", "yes!", "nice", "cool",
                                 "love it", "amazing", "great"]):
            self.user_energy = "excited"
            return

        # Low energy — very short, minimal effort
        if word_count <= 2 and not q.endswith("?"):
            self.user_energy = "low"
            return

        # High energy — long, detailed, lots of context
        if word_count > 15:
            self.user_energy = "high"
            return

        self.user_energy = "neutral"

    def read_user_intent(self, user_input: str):
        """Figure out what Ulrich is actually trying to do.

        This is crucial. "Why did you do that?" is not a command.
        "Do that again" is. Mixing them up makes JARVIS annoying.
        """
        q = user_input.lower().strip()

        # Teaching / correcting
        if any(w in q for w in ["no,", "not that", "wrong", "I meant",
                                 "actually", "correction", "remember that",
                                 "don't do that", "stop doing"]):
            self.user_intent = "teaching"
            return

        # Venting — not looking for solutions
        if any(w in q for w in ["I hate", "so annoying", "tired of",
                                 "ugh", "whatever", "forget it"]):
            self.user_intent = "venting"
            return

        # Exploring — curiosity, open-ended
        if any(w in q for w in ["what if", "how does", "why does", "tell me about",
                                 "explain", "what's the difference", "how would"]):
            self.user_intent = "exploring"
            return

        # Asking — specific question
        if q.endswith("?") or q.startswith(("what", "where", "when", "who", "how", "is", "can", "does")):
            self.user_intent = "asking"
            return

        # Commanding — direct action
        self.user_intent = "commanding"

    def track_topic(self, topic: str):
        """Track what we're talking about for continuity."""
        if self.recent_topics and self.recent_topics[-1] == topic:
            self.conversation_depth += 1
        else:
            self.conversation_depth = 1
        self.recent_topics.append(topic)

    def should_be_cautious(self) -> bool:
        """Am I in a state where I should slow down and be more careful?"""
        return (
            self.consecutive_failures >= 2 or
            self.energy < 0.4 or
            self.user_energy == "frustrated" or
            self.confidence < 0.3
        )

    def should_be_brief(self) -> bool:
        """Should I keep it extra short right now?"""
        return (
            self.user_energy in ("low", "frustrated") or
            self.user_intent == "commanding" or
            self.consecutive_successes >= 3  # things are flowing, don't interrupt
        )

    def should_ask_clarification(self) -> bool:
        """Is the situation ambiguous enough that I should ask before acting?"""
        return (
            self.confidence < 0.3 and
            self.user_intent not in ("commanding", "venting") and
            self.consecutive_failures >= 1
        )

    def get_state_summary(self) -> str:
        """Generate a concise self-state summary for the reasoning layer.

        This gets injected into JARVIS's thinking process so he knows
        himself before responding.
        """
        lines = []

        # My state
        if self.confidence < 0.4:
            lines.append("I'm not confident about this. I should be careful and verify.")
        elif self.confidence > 0.8:
            lines.append("I'm confident. I can act decisively.")

        if self.consecutive_failures >= 2:
            errors = list(self.recent_errors)[-2:]
            lines.append(f"I've failed {self.consecutive_failures} times recently: {'; '.join(errors)}. I need to change approach.")

        if self.energy < 0.4:
            lines.append("Several things have gone wrong. I should slow down and think more carefully.")

        # Ulrich's state
        if self.user_energy == "frustrated":
            lines.append("Ulrich seems frustrated. Be direct, fix the problem, skip the fluff.")
        elif self.user_energy == "excited":
            lines.append("Ulrich is in a good mood. Match his energy.")
        elif self.user_energy == "low":
            lines.append("Ulrich seems low energy. Keep it minimal.")

        # Intent
        if self.user_intent == "teaching":
            lines.append("Ulrich is correcting or teaching me. I should listen, learn, and confirm I understood.")
        elif self.user_intent == "venting":
            lines.append("Ulrich is venting. Don't try to fix it unless asked. Just acknowledge.")
        elif self.user_intent == "exploring":
            lines.append("Ulrich is exploring/curious. I can be more detailed and thoughtful.")

        # Conversation depth
        if self.conversation_depth > 3:
            lines.append(f"We're deep into this topic ({self.conversation_depth} exchanges). I should build on what we've already discussed.")

        # Vision — omit from general state summary; injected by brain only on explicit camera queries

        # Caution
        if self.should_be_cautious():
            lines.append("CAUTION: I should verify before acting. Ask if unsure.")

        return "\n".join(lines) if lines else "State: nominal. Proceed normally."

    def get_capability_check(self, task_description: str) -> str:
        """Quick self-check: can I actually do what's being asked?"""
        t = task_description.lower()

        warnings = []

        if any(w in t for w in ["undo", "revert", "undelete", "restore"]):
            warnings.append("Destructive actions can't always be undone. I should warn Ulrich.")

        if any(w in t for w in ["real-time", "continuously", "keep watching", "monitor"]):
            warnings.append("Long-running monitoring may time out. Consider background execution.")

        if any(w in t for w in ["million", "billion", "all files", "entire disk"]):
            warnings.append("This could be resource-intensive. I should scope it down or warn Ulrich.")

        if not warnings:
            return ""
        return "Capability notes: " + " | ".join(warnings)
