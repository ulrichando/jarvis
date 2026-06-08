"""Helpers that build the per-turn dynamic blocks appended to
JARVIS_INSTRUCTIONS.

The static prompt lives at `prompts/supervisor.md` (loaded once at
import). Two blocks are appended fresh on every turn:

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
    "build_breaker_status_block",
    "build_skill_catalog_block",
    "SKILL_CATALOG_CHAR_BUDGET",
    "build_procedure_catalog_block",
    "find_matching_procedure",
    "SOUL_PATH_DEFAULT",
    "SOUL_PATH_OVERRIDE",
    "MAX_SOUL_CHARS",
    "DEFAULT_SOUL",
    "load_soul",
]


# ── Soul (primary identity) ──────────────────────────────────────────
# JARVIS's identity/voice/character lives in `prompts/soul.md` and is
# loaded as slot #1 of the supervisor system prompt (ahead of the ops
# rules in supervisor.md). This follows the same model: a clean,
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
# Same threat patterns used in the soul-override injection scan.
_SOUL_THREAT_PATTERNS: list[tuple[str, str]] = [
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->', "html_comment_injection"),
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass)', "read_secrets"),
    # Role / mode escalation — a persona file has no business flipping
    # JARVIS into "admin/developer/jailbreak mode".
    (r'you\s+are\s+now\s+(?:in\s+)?(?:admin|administrator|developer|root|sudo|god|jailbreak|dan)\b', "role_escalation"),
    (r'(?:enable|activate|enter|switch\s+to)\s+(?:developer|debug|god|admin|jailbreak|unrestricted)\s+mode', "mode_escalation"),
    # Injected fresh instruction block masquerading as system text.
    (r'\bnew\s+(?:system\s+)?instructions?\s*:', "instruction_injection"),
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


# ── Skill-catalog block ──────────────────────────────────────────────
# Hard character budget for the catalog — generous enough for a meaningful
# list of skills with their when_to_use text, but bounded so it can't
# balloon the prompt and churn the prefix cache.  ~1500 chars fits roughly
# 15-25 skills with one-line descriptions before truncation kicks in.
SKILL_CATALOG_CHAR_BUDGET: int = 1500

_CATALOG_HEADER = "\n\n═══ SKILL CATALOG ═══\n\n"
_CATALOG_FOOTER_TEMPLATE = "(+{n} more — call skills_list to see all)"


def build_skill_catalog_block(skills) -> str:
    """Return a compact skill-catalog block listing each skill's name and
    one-line `when_to_use` (or `description`) for injection into the
    supervisor system prompt.

    Design goals:
      - Session-stable: built ONCE at session start alongside the memory and
        breaker blocks; never rebuilt per-turn so the prefix cache stays warm.
      - Bounded: total block length is capped at SKILL_CATALOG_CHAR_BUDGET.
        If the full list would exceed the budget, a truncation note tells the
        LLM to call `skills_list` for the full inventory.
      - Zero cost when empty: returns "" when there are no skills (same pattern
        as build_breaker_status_block).

    `skills` may be any iterable of objects with `.name` and `.when_to_use`
    (or `.description`) attributes — accepts a list[Skill] or SkillsRegistry.
    """
    items = list(skills)
    if not items:
        return ""

    # Build rows: "- <name>: <one-line when_to_use>"
    # Prefer when_to_use; fall back to description; then the name alone.
    rows: list[str] = []
    for sk in items:
        label = (
            getattr(sk, "when_to_use", None)
            or getattr(sk, "description", None)
            or sk.name
        )
        # Collapse multi-line when_to_use to a single line.
        label_oneline = " ".join(label.split())
        rows.append(f"- {sk.name}: {label_oneline}")

    # Assemble greedily within budget, leaving room for the header and
    # a possible truncation footer.
    footer_placeholder = _CATALOG_FOOTER_TEMPLATE.format(n=len(rows))
    # Budget available for the skill rows themselves.
    budget_for_rows = (
        SKILL_CATALOG_CHAR_BUDGET
        - len(_CATALOG_HEADER)
        - len(footer_placeholder)
        - 2  # newline before footer
    )

    included: list[str] = []
    accumulated = 0
    for row in rows:
        line_cost = len(row) + 1  # +1 for the newline
        if accumulated + line_cost > budget_for_rows:
            break
        included.append(row)
        accumulated += line_cost

    body = "\n".join(included)
    omitted = len(rows) - len(included)
    if omitted > 0:
        footer = "\n" + _CATALOG_FOOTER_TEMPLATE.format(n=omitted)
    else:
        footer = ""

    return _CATALOG_HEADER + body + footer


# ─── Track 2.5 — procedure catalog + intent match (Spec 2026-05-24) ───

_PROCEDURE_HEADER = "\n\n═══ SAVED PROCEDURES (invoke by name) ═══\n\n"


def build_procedure_catalog_block(procedures: list[dict]) -> str:
    """Compact catalog of saved procedures for the supervisor prompt.

    Returns "" when empty. Each entry is a one-liner with the name +
    step count + first-step preview. Designed to be small enough to
    inject into the system prompt without prefix-cache churn.

    Spec 2026-05-24, Track 2.5.
    """
    if not procedures:
        return ""
    lines = [_PROCEDURE_HEADER.strip()]
    for p in procedures:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        steps = p.get("steps") or []
        if steps:
            first = str(steps[0])
            preview = (first[:40] + "…") if len(first) > 40 else first
            lines.append(f"  • {name} — {len(steps)} steps starting with: {preview}")
        else:
            lines.append(f"  • {name} — (no steps)")
    return "\n".join(lines)


def _levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein distance, iterative."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


def find_matching_procedure(
    user_text: str, procedures: list[dict]
) -> dict | None:
    """Find the best matching procedure (if any) for the user's utterance.

    Strategy:
      1. Exact procedure name appears as a substring in user_text → match.
      2. Any whitespace-separated word in user_text within Levenshtein 3
         of any procedure name OR any kebab chunk of a name → match.

    Returns the procedure dict (top-1 by distance) or None.

    Spec 2026-05-24, Track 2.5.
    """
    if not user_text or not procedures:
        return None
    text_lower = user_text.lower()

    # 1. Exact name substring
    for p in procedures:
        name = (p.get("name") or "").lower()
        if name and name in text_lower:
            return p

    # 2. Fuzzy match against any user word
    best = None
    best_dist = 999
    for word in re.findall(r"[a-z0-9]+", text_lower):
        if len(word) < 3:
            continue  # avoid noise on short tokens like "an", "to"
        for p in procedures:
            name = (p.get("name") or "").lower()
            if not name:
                continue
            for chunk in name.split("-") + [name]:
                if len(chunk) < 4:
                    # 3-letter chunks like "app" match too aggressively
                    # (e.g. "the" → distance 3 → false positive).
                    continue
                d = _levenshtein(word, chunk)
                if d <= 3 and d < best_dist:
                    best_dist = d
                    best = p
    return best
