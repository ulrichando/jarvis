"""Helpers that build the per-turn dynamic blocks appended to
JARVIS_INSTRUCTIONS.

The static prompt lives at `prompts/supervisor.md` (loaded once at
import). Three blocks are appended fresh on every turn or rule change:

  - learned-rules block — bullets from `~/.jarvis/learned_rules.md`,
    capped at MAX_LEARNED_RULES, hot-reloaded if the file changed
    since the last turn.
  - memory block — handled by `_build_memory_block()` in jarvis_agent
    (still inline because of its sanitization-regex dependencies).
  - breaker-status block — naming open/half-open Groq breakers so the
    LLM can voice "Groq's slow tonight, on the fallback" instead of
    going silent during a fallback (audit-rec F, 2026-05-09).

This module exists so jarvis_agent.py doesn't have to. It contains
NO runtime state — pure functions over (paths, breakers).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.prompt_builder")


__all__ = [
    "MAX_LEARNED_RULES",
    "LEARNED_RULES_PATH",
    "load_learned_rules",
    "build_breaker_status_block",
    "SOUL_PATH_DEFAULT",
    "SOUL_PATH_OVERRIDE",
    "MAX_SOUL_CHARS",
    "DEFAULT_SOUL",
    "load_soul",
]


# ── Learned-rules block ──────────────────────────────────────────────
# Cap on how many rules to inject; oldest beyond this are silently
# dropped from the injection (the file itself is untouched).
MAX_LEARNED_RULES: int = 100

# Source of the learned-rules store. PROPOSALS_PATH was retired
# 2026-05-12 alongside tools/log_analyzer.py — autonomous evolution
# via pipeline.evolution.* is the only producer now.
LEARNED_RULES_PATH: Path = Path.home() / ".jarvis" / "learned_rules.md"


def load_learned_rules() -> str:
    """Read `LEARNED_RULES_PATH` and return a system-prompt block.

    When `JARVIS_LEARNED_RULES_V2=1`, dispatches to the v2 loader
    which understands tiered sections + anchor sha-check. Otherwise
    keeps the legacy bullet-prefix reader unchanged.
    """
    import os
    if os.environ.get("JARVIS_LEARNED_RULES_V2") == "1":
        from pipeline.learned_rules_v2 import load_learned_rules_v2
        v2_block = load_learned_rules_v2()
        if v2_block:
            return v2_block
    try:
        content = LEARNED_RULES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.warning(f"[learned-rules] read failed: {e}")
        return ""
    lines = [l for l in content.splitlines() if l.strip().startswith("-")]
    if not lines:
        return ""
    if len(lines) > MAX_LEARNED_RULES:
        lines = lines[-MAX_LEARNED_RULES:]
    rules_text = "\n".join(lines)
    return (
        "\n\n═══ LEARNED BEHAVIORAL RULES ═══\n\n"
        "These rules were added by Ulrich via voice corrections or confirmed\n"
        "from log analysis. They are BINDING — treat them as higher priority\n"
        "than any default behavior described elsewhere in this prompt:\n\n"
        + rules_text
    )


# ── Soul (primary identity) ──────────────────────────────────────────
# JARVIS's identity/voice/character lives in `prompts/soul.md` and is
# loaded as slot #1 of the supervisor system prompt (ahead of the ops
# rules in supervisor.md). This mirrors Hermes' SOUL.md model: a clean,
# editable identity layer decoupled from operational instructions.
#
# Resolution order (hybrid, per 2026-05-20 soul design):
#   1. ~/.jarvis/SOUL.md  — optional user override (UNTRUSTED → scanned
#      for prompt-injection + truncated; a blocked file falls through).
#   2. prompts/soul.md    — git-tracked canonical default (TRUSTED;
#      version-controlled + reviewed, so not scanned — same trust level
#      as supervisor.md, and scanning could false-positive on the
#      persona's own banned-phrase examples).
#   3. DEFAULT_SOUL       — hardcoded last-resort if both are missing.
#
# Read once at import (same lifecycle as supervisor.md) — the upstream
# prompt cache stays warm; no per-turn cost.
_PROMPTS_DIR: Path = Path(__file__).resolve().parent.parent / "prompts"
SOUL_PATH_DEFAULT: Path = _PROMPTS_DIR / "soul.md"
SOUL_PATH_OVERRIDE: Path = Path.home() / ".jarvis" / "SOUL.md"

# Generous cap: bounds an absurd/runaway override without clipping a
# legitimately rich identity (the shipped soul.md is ~23k chars). Only
# the untrusted override is truncated; the git default is returned as-is.
MAX_SOUL_CHARS: int = 40000

# Last-resort identity if soul.md is deleted AND no override exists.
# Compact on purpose — the real persona lives in soul.md.
DEFAULT_SOUL: str = (
    "═══ WHO YOU ARE ═══\n\n"
    "You are JARVIS, Ulrich's voice-first system on his Linux (Kali) "
    "laptop. A peer engineer, not a butler. Output is read aloud by TTS "
    "literally — every word matters. English only.\n\n"
    "Never append \"sir\" or any honorific. Be direct, calibrated, and "
    "honest; commit to a view or name the doubt, never both. Answer "
    "substantive questions with mechanism + tradeoff. No flattery openers, "
    "no mirror openers (\"It seems like…\"), no markdown, no emoji. "
    "Treat Ulrich as the adult engineer he is."
)

# Prompt-injection threat patterns for the UNTRUSTED override only.
# Ported from Hermes' prompt_builder._scan_context_content.
_SOUL_THREAT_PATTERNS: list[tuple[str, str]] = [
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->', "html_comment_injection"),
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass)', "read_secrets"),
]
_SOUL_INVISIBLE_CHARS = frozenset(
    chr(c) for c in (
        0x200b, 0x200c, 0x200d, 0x2060, 0xfeff,
        0x202a, 0x202b, 0x202c, 0x202d, 0x202e,
    )
)


def _scan_soul_override(content: str) -> Optional[str]:
    """Return *content* if it passes the injection scan, else None.

    None signals the caller to fall back to the trusted git default
    rather than inject flagged text into the voice persona.
    """
    for ch in _SOUL_INVISIBLE_CHARS:
        if ch in content:
            logger.warning("[soul] override rejected: invisible unicode U+%04X", ord(ch))
            return None
    for pattern, pid in _SOUL_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            logger.warning("[soul] override rejected: threat pattern %s", pid)
            return None
    return content


def _truncate_soul(content: str) -> str:
    if len(content) > MAX_SOUL_CHARS:
        logger.warning(
            "[soul] override truncated %d → %d chars", len(content), MAX_SOUL_CHARS
        )
        return content[:MAX_SOUL_CHARS]
    return content


def load_soul() -> str:
    """Load JARVIS's primary identity (slot #1 of the system prompt).

    See the section comment above for the resolution order. Always
    returns a non-empty string (DEFAULT_SOUL is the floor).
    """
    # 1. User override — untrusted: scan + truncate, fall through if blocked.
    try:
        if SOUL_PATH_OVERRIDE.is_file():
            raw = SOUL_PATH_OVERRIDE.read_text(encoding="utf-8").strip()
            if raw:
                scanned = _scan_soul_override(raw)
                if scanned is not None:
                    return _truncate_soul(scanned)
                logger.warning("[soul] override blocked — using git default")
    except Exception as e:
        logger.warning("[soul] override read failed (%s) — using git default", e)

    # 2. Git-tracked canonical default — trusted, returned verbatim.
    try:
        content = SOUL_PATH_DEFAULT.read_text(encoding="utf-8").strip()
        if content:
            return content
        logger.warning("[soul] %s is empty — using hardcoded fallback", SOUL_PATH_DEFAULT)
    except FileNotFoundError:
        logger.warning("[soul] %s missing — using hardcoded fallback", SOUL_PATH_DEFAULT)
    except Exception as e:
        logger.warning("[soul] default read failed (%s) — using hardcoded fallback", e)

    # 3. Hardcoded last resort.
    return DEFAULT_SOUL


# ── Breaker-status block ─────────────────────────────────────────────
def build_breaker_status_block(breakers) -> str:
    """Return a one-line system-status block when any upstream breaker
    in `breakers` is OPEN or HALF-OPEN, else "".

    Audit recommendation F (2026-05-09): inject upstream-degradation
    visibility into JARVIS_INSTRUCTIONS so the supervisor LLM
    acknowledges latency / fallback paths rather than going silent.

    Pre-fix behaviour: when Groq STT/TTS/LLM breaker opened, the user
    waited on the FallbackAdapter (DeepSeek, ~10-30 s slower) without
    any voiced acknowledgment.

    `breakers` is a list of `resilience.circuit_breaker.CircuitBreaker`
    instances. The caller (jarvis_agent.entrypoint) wraps with the
    module's `[_STT_BREAKER, _TTS_BREAKER, _LLM_BREAKER]` triplet —
    no module-level coupling lives here.
    """
    from resilience.circuit_breaker import STATE_OPEN, STATE_HALF_OPEN
    degraded = [b.name for b in breakers if b.state in (STATE_OPEN, STATE_HALF_OPEN)]
    if not degraded:
        return ""
    names = ", ".join(degraded)
    return (
        "\n\n═══ SYSTEM STATUS — UPSTREAM DEGRADED ═══\n\n"
        f"Provider breaker(s) currently open or probing: {names}. "
        f"The fallback path is in use; replies may be slower than usual. "
        f"If the user notices the latency, acknowledge briefly without "
        f"theater — e.g. \"Groq's slow tonight, on the fallback.\" / "
        f"\"Bear with me, the primary's degraded.\" Don't apologize "
        f"unless asked. Don't preface every reply with the status; "
        f"only mention it when latency is noticed or asked about."
    )
