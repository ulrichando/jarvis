"""Jarvis Language System — English comprehension and response generation.

This gives Jarvis the ability to:
1. Understand English input (parse intent, extract keywords)
2. Match input to known knowledge
3. Compose natural responses from facts (not raw data dumps)
4. Track what's been said to avoid repetition
"""

from __future__ import annotations


# --- Response Templates ---
# These are sentence patterns Jarvis uses to compose answers from facts.

RESPONSE_PATTERNS = {
    "definition": [
        "{subject} is {definition}.",
        "{subject} means {definition}.",
        "The word '{subject}' refers to {definition}.",
    ],
    "capital": [
        "The capital of {country} is {capital}.",
        "{capital} is the capital of {country}.",
    ],
    "fact": [
        "{fact}.",
        "I know that {fact}.",
        "From what I've learned: {fact}.",
    ],
    "math": [
        "The answer is {result}.",
        "{expression} equals {result}.",
        "That's {result}.",
    ],
    "identity": [
        "{fact}",
    ],
    "unknown": [
        "I don't know about that yet. Can you teach me?",
        "I haven't learned about that. Tell me more and I'll remember it.",
        "That's a gap in my knowledge. What should I know about it?",
    ],
    "greeting": [
        "Hey! What's on your mind?",
        "Hi there. What do you need?",
        "Hey. Ready to go.",
    ],
    "thanks": [
        "Anytime.",
        "No problem.",
        "Sure thing.",
    ],
    "visual": [
        "I can see: {description}",
        "Looking through my camera: {description}",
        "{description}",
    ],
}

# --- Intent Detection ---

def detect_intent(text: str) -> tuple[str, dict]:
    """Detect what the user wants and extract key entities.

    Returns (intent, entities) where intent is one of:
    greeting, farewell, question, teach, command, describe, math, identity, thanks
    """
    q = text.lower().strip()
    words = q.split()

    # Greetings and social
    greetings = {"hello", "hi", "hey", "howdy", "greetings", "sup", "yo", "morning", "evening"}
    greeting_phrases = {
        "good morning", "good evening", "good afternoon", "good day",
        "what's up", "whats up", "how are you", "how's it going",
        "how are you doing", "how do you do", "what's good", "wassup",
    }
    if q in greetings or q in greeting_phrases or (len(words) <= 3 and words[0] in greetings):
        return "greeting", {}
    if q.startswith("how are") or q.startswith("how's it") or q.startswith("how you"):
        return "greeting", {}

    # Thanks
    if any(w in q for w in ["thank", "thanks", "thx", "cheers"]):
        return "thanks", {}

    # Farewell
    if q in ("bye", "goodbye", "later", "see you", "good night", "gn"):
        return "farewell", {}

    # Identity questions
    if any(p in q for p in ["who are you", "what are you", "what can you do", "tell me about yourself"]):
        return "identity", {}

    # Teaching / correction
    if any(p in q for p in ["remember that", "learn this", "the answer is", "actually it's",
                             "no it's", "that's wrong"]):
        return "teach", {"content": text}

    # Time/date questions — handle before math to avoid "time" matching math
    import re
    if any(p in q for p in ["what time", "what's the time", "what date", "what day"]):
        import datetime
        now = datetime.datetime.now()
        return "question", {"type": "time", "subject": now.strftime("%I:%M %p, %A %B %d, %Y")}

    # Math — only match when there are actual numbers involved
    math_match = re.search(r'(\d+)\s*([\+\-\*x×/÷]|plus|minus|times|divided by|multiplied by)\s*(\d+)', q)
    if math_match:
        return "math", {"expression": q}
    # "calculate X" or "how much is X" but only if numbers are present
    if any(p in q for p in ["calculate ", "how much is "]) and re.search(r'\d', q):
        return "math", {"expression": q}

    # Capital questions
    capital_match = re.search(r'capital\s+of\s+(\w[\w\s]*)', q)
    if capital_match:
        return "question", {"type": "capital", "country": capital_match.group(1).strip()}

    # Definition questions
    if any(q.startswith(p) for p in ["what is ", "what are ", "what does ", "define ", "meaning of "]):
        subject = q
        for prefix in ["what is a ", "what is an ", "what is the ", "what is ", "what are ",
                        "what does ", "define ", "meaning of "]:
            if q.startswith(prefix):
                subject = q[len(prefix):].rstrip("?").strip()
                break
        # Strip trailing "mean" / "means"
        for suffix in [" mean", " means"]:
            if subject.endswith(suffix):
                subject = subject[:-len(suffix)].strip()
        return "question", {"type": "definition", "subject": subject}

    # Who/where/when questions
    if q.startswith(("who ", "where ", "when ", "why ", "how ")):
        return "question", {"type": "general", "subject": q.rstrip("?").strip()}

    # Yes/no questions
    if q.endswith("?"):
        return "question", {"type": "general", "subject": q.rstrip("?").strip()}

    # Describe what you see
    if any(p in q for p in ["what do you see", "what can you see", "describe what",
                             "look around", "what's in front"]):
        return "describe", {}

    # Default: treat as a statement or command
    if len(words) <= 3:
        return "command", {"content": text}

    return "question", {"type": "general", "subject": q}


def compose_response(intent: str, facts: list[str], entities: dict) -> str:
    """Compose a natural English response from intent, facts, and entities.

    This is the key function that turns raw knowledge into speech.
    """
    import random

    if intent == "greeting":
        return random.choice(RESPONSE_PATTERNS["greeting"])

    if intent == "thanks":
        return random.choice(RESPONSE_PATTERNS["thanks"])

    if intent == "farewell":
        return random.choice(["Later.", "See you.", "I'll be here."])

    if intent == "identity":
        identity_facts = [f for f in facts if any(w in f.lower() for w in ["jarvis", "i am", "my creator", "i learn", "i have"])]
        if identity_facts:
            return " ".join(identity_facts[:3])
        return "I'm Jarvis — an autonomous AI that learns from experience."

    if intent == "teach":
        content = entities.get("content", "")
        return f"Got it. I'll remember that."

    if intent == "question" and entities.get("type") == "time":
        return f"It's {entities.get('subject', 'unknown')}."

    if intent == "math":
        # Try to compute
        result = _try_math(entities.get("expression", ""))
        if result is not None:
            return random.choice(RESPONSE_PATTERNS["math"]).format(
                result=result, expression=entities.get("expression", ""))
        # Check facts
        math_facts = [f for f in facts if "equals" in f or "plus" in f or "times" in f]
        if math_facts:
            return math_facts[0]

    if intent == "describe":
        visual_facts = [f for f in facts if any(w in f.lower() for w in ["see", "detect", "scene", "camera", "object"])]
        if visual_facts:
            return random.choice(RESPONSE_PATTERNS["visual"]).format(description=visual_facts[0])
        return "I'm looking but my camera might not be active right now."

    if intent == "question":
        qtype = entities.get("type", "general")

        if qtype == "capital":
            country = entities.get("country", "").strip().lower()
            # Look for "The capital of X is Y" pattern first
            for f in facts:
                fl = f.lower()
                if "capital of" in fl and country in fl and " is " in fl:
                    return f
            # Look for "X has_property capital Y"
            for f in facts:
                fl = f.lower()
                if country in fl and "capital" in fl:
                    # Parse "X has_property capital Y"
                    if "has_property capital" in fl:
                        parts = f.split("capital")
                        if len(parts) >= 2:
                            capital = parts[-1].strip()
                            return f"The capital of {country.title()} is {capital}."
                    return f
            return f"I don't know the capital of {country.title()} yet. Can you tell me?"

        if qtype == "definition":
            subject = entities.get("subject", "").strip().lower()
            # Priority 1: "X is <definition>" direct match
            for f in facts:
                fl = f.lower()
                if fl.startswith(f"{subject} is ") or f"'{subject}' means:" in fl:
                    return f
            # Priority 2: "The word 'X' means: Y"
            for f in facts:
                fl = f.lower()
                if f"'{subject}'" in fl and "means" in fl:
                    return f
            # Priority 3: any fact containing the subject with "is" or "means"
            for f in facts:
                fl = f.lower()
                if subject in fl and (" is " in fl or " means" in fl):
                    return f
            # Priority 4: any fact containing the subject
            for f in facts:
                if subject in f.lower():
                    return f
            return f"I haven't learned what '{subject}' means yet. Can you explain it to me?"

        # General question
        if facts:
            relevant = [f for f in facts if not f.startswith("[") and "Insufficient" not in f]
            if relevant:
                parts = []
                for f in relevant[:2]:
                    f = _rephrase_user_fact(f).strip().rstrip(".")
                    parts.append(f)
                return ". ".join(parts) + "."

    # Fallback
    if facts:
        clean = [f for f in facts if "Insufficient" not in f and len(f) > 5]
        if clean:
            return _rephrase_user_fact(clean[0])

    return random.choice(RESPONSE_PATTERNS["unknown"])


def _rephrase_user_fact(fact: str) -> str:
    """Rephrase facts about 'user' into second person.

    'user likes Python' → 'You like Python'
    'user name is Ulrich' → 'Your name is Ulrich'
    """
    f = fact.strip()
    fl = f.lower()

    # "user likes X" → "You like X"
    if fl.startswith("user likes "):
        return "You like " + f[len("user likes "):]
    if fl.startswith("user dislikes "):
        return "You don't like " + f[len("user dislikes "):]
    if fl.startswith("user prefers "):
        return "You prefer " + f[len("user prefers "):]

    # "user X is Y" → "Your X is Y"
    if fl.startswith("user "):
        rest = f[len("user "):]
        if " is " in rest.lower():
            return "Your " + rest

    # "my X is Y" → keep as-is or rephrase
    if fl.startswith("my "):
        return "Your " + f[len("my "):]

    return f


def _try_math(expression: str) -> float | None:
    """Try to evaluate a simple math expression."""
    import re
    q = expression.lower().strip().rstrip("?")

    # Remove question words
    for prefix in ["what is ", "calculate ", "how much is ", "what's "]:
        if q.startswith(prefix):
            q = q[len(prefix):]

    # Replace words with operators
    q = q.replace("plus", "+").replace("minus", "-")
    q = q.replace("times", "*").replace("multiplied by", "*").replace("x", "*").replace("×", "*")
    q = q.replace("divided by", "/").replace("÷", "/")

    # Only allow safe characters
    q = q.strip()
    if re.match(r'^[\d\s\+\-\*/\.\(\)]+$', q):
        try:
            result = eval(q)
            if isinstance(result, float) and result == int(result):
                return int(result)
            return result
        except Exception:
            pass
    return None


# --- Question Tracking ---

class QuestionTracker:
    """Tracks what Jarvis has already asked to prevent repetition."""

    def __init__(self, max_history: int = 100):
        self._asked: set[str] = set()
        self._history: list[str] = []
        self.max_history = max_history

    def has_asked(self, question: str) -> bool:
        key = question.lower().strip()
        return key in self._asked

    def record(self, question: str):
        key = question.lower().strip()
        self._asked.add(key)
        self._history.append(key)
        if len(self._history) > self.max_history:
            old = self._history.pop(0)
            self._asked.discard(old)

    def forget_oldest(self, n: int = 10):
        for _ in range(min(n, len(self._history))):
            old = self._history.pop(0)
            self._asked.discard(old)
