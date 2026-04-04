"""UnderstandingEngine — NLU without an LLM.

Uses regex-based NER, keyword intent classification, and lexicon sentiment.
The reasoner/memory arguments are accepted for future deep-understanding
paths but the core methods work entirely offline.
"""

import re
from typing import Optional

# ── Intent keywords ───────────────────────────────────────────────────

_QUESTION_STARTERS = {
    "what", "who", "where", "when", "why", "how", "which", "is", "are",
    "was", "were", "do", "does", "did", "can", "could", "would", "should",
    "will", "shall", "have", "has", "had", "tell me",
}

_COMMAND_VERBS = {
    "run", "open", "start", "stop", "kill", "install", "remove", "delete",
    "create", "make", "build", "deploy", "scan", "find", "search", "show",
    "list", "set", "get", "update", "upgrade", "download", "upload",
    "move", "copy", "rename", "edit", "write", "read", "execute", "launch",
    "send", "check", "fix", "debug", "test", "compile", "monitor", "watch",
    "restart", "reboot", "shutdown", "connect", "disconnect", "enable",
    "disable", "configure", "backup", "restore", "ssh", "ping", "nmap",
    "curl", "wget", "git", "docker", "sudo",
}

_GREETINGS = {
    "hi", "hello", "hey", "yo", "sup", "morning", "evening", "afternoon",
    "good morning", "good evening", "good afternoon", "good night",
    "what's up", "whats up", "howdy", "greetings", "hola", "bonjour",
    "salut", "wassup", "g'day",
}

# ── Sentiment lexicon (lightweight) ──────────────────────────────────

_POS_WORDS = {
    "good", "great", "awesome", "excellent", "amazing", "wonderful",
    "fantastic", "perfect", "love", "like", "best", "happy", "nice",
    "brilliant", "cool", "thanks", "thank", "beautiful", "superb",
    "incredible", "outstanding", "impressive", "sweet", "solid",
    "helpful", "useful", "well", "better", "improved", "fixed",
}

_NEG_WORDS = {
    "bad", "terrible", "awful", "horrible", "worst", "hate", "broken",
    "wrong", "error", "fail", "failed", "failure", "crash", "crashed",
    "bug", "slow", "ugly", "useless", "stupid", "annoying", "frustrated",
    "sucks", "crap", "garbage", "trash", "mess", "worse", "fucked",
    "shit", "damn", "dumb", "pathetic", "disappointing",
}

# ── Entity patterns ──────────────────────────────────────────────────

_ENTITY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ip_address",   re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")),
    ("ipv6_address", re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")),
    ("mac_address",  re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")),
    ("email",        re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("url",          re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)),
    ("domain",       re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|dev|ai|co|gov|edu|info|xyz|me|app|tech|ru|cn|uk)\b")),
    ("file_path",    re.compile(r"(?:/[\w.~-]+)+(?:\.\w+)?|~(?:/[\w.~-]+)+")),
    ("port",         re.compile(r"\bport\s+(\d{1,5})\b", re.IGNORECASE)),
    ("cve",          re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)),
    ("hash_md5",     re.compile(r"\b[a-fA-F0-9]{32}\b")),
    ("hash_sha256",  re.compile(r"\b[a-fA-F0-9]{64}\b")),
    ("number",       re.compile(r"\b\d+(?:\.\d+)?\b")),
    ("quoted_string", re.compile(r'"([^"]+)"|\'([^\']+)\'')),
]


class UnderstandingEngine:
    """Lightweight NLU: intent classification, entity extraction, sentiment."""

    def __init__(self, reasoner=None, memory=None):
        self.reasoner = reasoner
        self.memory = memory

    # ── Main entry ────────────────────────────────────────────────────

    def understand(self, text: str) -> dict:
        """Full NLU pass: intent + entities + sentiment."""
        return {
            "intent": self.classify_intent(text),
            "entities": self.extract_entities(text),
            "sentiment": self.get_sentiment(text),
            "text": text,
        }

    # ── Intent classification ─────────────────────────────────────────

    def classify_intent(self, text: str) -> str:
        """Classify text as question / command / greeting / statement."""
        stripped = text.strip()
        lower = stripped.lower()

        # Greeting check (exact or starts-with)
        if lower in _GREETINGS:
            return "greeting"
        for g in _GREETINGS:
            if lower.startswith(g) and (len(lower) == len(g) or lower[len(g)] in " ,!."):
                return "greeting"

        # Question — ends with ? or starts with question word
        if stripped.endswith("?"):
            return "question"
        first_word = lower.split()[0] if lower.split() else ""
        first_two = " ".join(lower.split()[:2]) if len(lower.split()) >= 2 else ""
        if first_word in _QUESTION_STARTERS or first_two in _QUESTION_STARTERS:
            return "question"

        # Command — starts with verb or looks imperative
        if first_word in _COMMAND_VERBS:
            return "command"
        # "please <verb>" pattern
        if first_word == "please" and len(lower.split()) > 1:
            second = lower.split()[1]
            if second in _COMMAND_VERBS:
                return "command"

        return "statement"

    # ── Entity extraction ─────────────────────────────────────────────

    def extract_entities(self, text: str) -> list[dict]:
        """Extract named entities using regex patterns.

        Returns list of {type, value, start, end}.
        """
        entities = []
        seen_spans: set[tuple[int, int]] = set()

        for etype, pattern in _ENTITY_PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                # Avoid overlapping entities (prefer earlier, longer patterns)
                if any(s <= span[0] < e or s < span[1] <= e for s, e in seen_spans):
                    continue
                # For grouped patterns, prefer the group content
                value = m.group(1) or m.group(0) if m.lastindex else m.group(0)
                entities.append({
                    "type": etype,
                    "value": value,
                    "start": m.start(),
                    "end": m.end(),
                })
                seen_spans.add(span)

        # Sort by position
        entities.sort(key=lambda e: e["start"])
        return entities

    # ── Sentiment ─────────────────────────────────────────────────────

    def get_sentiment(self, text: str) -> str:
        """Simple lexicon-based sentiment: positive / negative / neutral."""
        words = set(re.findall(r"\b\w+\b", text.lower()))
        pos = len(words & _POS_WORDS)
        neg = len(words & _NEG_WORDS)
        diff = pos - neg
        if diff >= 2 or (diff >= 1 and neg == 0):
            return "positive"
        if diff <= -2 or (diff <= -1 and pos == 0):
            return "negative"
        if pos > 0 and neg > 0:
            return "mixed"
        return "neutral"
