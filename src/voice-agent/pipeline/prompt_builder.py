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
from pathlib import Path

logger = logging.getLogger("jarvis.prompt_builder")


# ── Learned-rules block ──────────────────────────────────────────────
# Cap on how many rules to inject; oldest beyond this are silently
# dropped from the injection (the file itself is untouched).
MAX_LEARNED_RULES: int = 100

# Sources for the learned-rules + log-analyzer-proposal stores.
LEARNED_RULES_PATH: Path = Path.home() / ".jarvis" / "learned_rules.md"
PROPOSALS_PATH:     Path = Path.home() / ".jarvis" / "learned_rules.proposals.md"


def load_learned_rules() -> str:
    """Read `LEARNED_RULES_PATH` and return a system-prompt block.

    Returns "" if the file is missing or empty — caller appends this
    to the instruction string so an empty return is harmless.
    """
    try:
        content = LEARNED_RULES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.warning(f"[learned-rules] read failed: {e}")
        return ""
    # Only lines that look like bullet points (start with '-')
    lines = [l for l in content.splitlines() if l.strip().startswith("-")]
    if not lines:
        return ""
    # Keep the most recent MAX_LEARNED_RULES; oldest are silently dropped
    # from the injection (not from the file).
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


def count_pending_proposals() -> int:
    """Return the number of PENDING rule proposals. 0 on any error.

    Lazy-imports `tools.log_analyzer.count_pending` so this module
    doesn't pull tool surface at import time.
    """
    try:
        from tools.log_analyzer import count_pending
        return count_pending()
    except Exception:
        return 0


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
