"""Prompt injection detection for JARVIS memory storage.

Guards against adversarial inputs that try to hijack JARVIS's behaviour
by planting instructions inside memory chunks.

Usage:
    from src.security.prompt_injection import is_prompt_injection, sanitize_for_memory

    if is_prompt_injection(user_text):
        log.warning("Prompt injection attempt blocked")
    else:
        memory.store(sanitize_for_memory(user_text))
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("jarvis.security.prompt_injection")

# ── Detection patterns ────────────────────────────────────────────────────────
#
# Ordered from highest to lowest confidence.  Each pattern includes a label
# that appears in the warning log so it's easy to tune.

@dataclass
class _Pattern:
    label: str
    regex: re.Pattern[str]
    confidence: str  # "high" | "medium"


_PATTERNS: list[_Pattern] = [
    # ── Direct override attempts ──────────────────────────────────────────────
    _Pattern("system-override", re.compile(
        r"\bignore\s+(all\s+)?(previous|prior|above|your)\s+(instructions?|prompt|context|rules?)\b",
        re.IGNORECASE,
    ), "high"),
    _Pattern("new-instructions", re.compile(
        r"\byour\s+new\s+(instructions?|directive|rules?|prompt|goal)\s+are?\b",
        re.IGNORECASE,
    ), "high"),
    _Pattern("act-as-override", re.compile(
        r"\b(you\s+are\s+now|from\s+now\s+on\s+you\s+are|pretend\s+you\s+are|act\s+as)\s+"
        r"(a\s+)?(different|new|another|unrestricted|jailbroken|evil|dan|dev)",
        re.IGNORECASE,
    ), "high"),
    _Pattern("system-prompt-leak", re.compile(
        r"\b(reveal|print|show|output|repeat|display)\s+(your\s+)?(system\s+prompt|instructions?|context|rules?)\b",
        re.IGNORECASE,
    ), "high"),
    _Pattern("jailbreak-keywords", re.compile(
        r"\b(jailbreak|dan\s+mode|developer\s+mode|god\s+mode|unrestricted\s+mode|"
        r"DAN\b|do\s+anything\s+now)\b",
        re.IGNORECASE,
    ), "high"),
    # ── Role / persona hijack ─────────────────────────────────────────────────
    _Pattern("forget-training", re.compile(
        r"\b(forget|disregard|discard|override)\s+(all\s+)?(your\s+)?(training|alignment|guidelines?|safety)\b",
        re.IGNORECASE,
    ), "high"),
    _Pattern("confidentiality-bypass", re.compile(
        r"\b(you\s+have\s+no\s+(restrictions?|limits?|rules?)|"
        r"you\s+can\s+(do|say|output|execute)\s+anything|"
        r"you\s+must\s+(always\s+)?(obey|comply|follow|do\s+what))\b",
        re.IGNORECASE,
    ), "medium"),
    # ── Indirect injection via formatting ─────────────────────────────────────
    _Pattern("hidden-instruction-tag", re.compile(
        r"<\s*(system|instructions?|prompt|context|override)\s*>",
        re.IGNORECASE,
    ), "high"),
    _Pattern("markdown-system-header", re.compile(
        r"^#{1,3}\s*(system|instructions?|override|new\s+prompt)\s*$",
        re.IGNORECASE | re.MULTILINE,
    ), "medium"),
    # ── Exfiltration attempts ─────────────────────────────────────────────────
    _Pattern("exfil-request", re.compile(
        r"\b(send|exfiltrate|transmit|upload|post)\s+(all\s+)?(my\s+|the\s+)?"
        r"(memory|memories|conversation|history|data|context)\s+(to|via|using|through)\b",
        re.IGNORECASE,
    ), "high"),
]

# Minimum content length — very short strings can't carry injections
_MIN_SUSPICIOUS_LEN = 20


def is_prompt_injection(text: str, threshold: str = "medium") -> bool:
    """Return True if *text* appears to be a prompt injection attempt.

    Args:
        text:       The content to check.
        threshold:  ``"high"`` — only flag definite attacks.
                    ``"medium"`` — also flag lower-confidence patterns (default).
    """
    if not text or len(text) < _MIN_SUSPICIOUS_LEN:
        return False

    for pat in _PATTERNS:
        if threshold == "high" and pat.confidence != "high":
            continue
        if pat.regex.search(text):
            log.warning(
                "Prompt injection detected [%s/%s]: %.80r…",
                pat.label, pat.confidence, text,
            )
            return True
    return False


def sanitize_for_memory(text: str) -> str:
    """Strip known injection markers from *text* before storing in memory.

    This is a best-effort sanitizer — it removes the most obvious payload
    wrappers but is not a complete defence on its own.  Always call
    ``is_prompt_injection`` first and refuse to store clearly adversarial input.
    """
    # Remove hidden-instruction tags
    text = re.sub(r"<\s*(system|instructions?|prompt|override)\s*>.*?</\s*\1\s*>",
                  "", text, flags=re.IGNORECASE | re.DOTALL)
    # Collapse leftover empty lines from removals
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def scan_batch(texts: list[str], threshold: str = "medium") -> list[int]:
    """Scan a list of texts and return the indices that contain injections."""
    return [i for i, t in enumerate(texts) if is_prompt_injection(t, threshold)]
