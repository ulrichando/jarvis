# src/voice-agent/tests/test_memory_anchor.py
"""Smoke test for the YOU-HAVE-MEMORY supervisor-prompt anchor.

The anchor exists to override the LLM's training-data prior that
'I'm a conversational AI without memory' — replacing it with a
short, naturally-phrased statement that mirrors what Anthropic
auto-injects with their memory tool. See spec:
docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md
"""
from __future__ import annotations


def test_memory_anchor_present_in_supervisor_prompt():
    """The YOU-HAVE-MEMORY block must be in JARVIS_INSTRUCTIONS so
    the supervisor LLM sees it on every turn."""
    import jarvis_agent

    instr = jarvis_agent.JARVIS_INSTRUCTIONS
    assert "═══ YOU HAVE MEMORY ═══" in instr, (
        "Anchor header missing — Phase 1 of memory-layer fix not in place"
    )
    # The two key tools must be named in the anchor so the LLM
    # cross-references them when temped to deny memory. Since the
    # file-backed swap (2026-05-21) the durable-write tool is
    # `memory(action, target, …)`; transcript search is
    # `session_search(query)` (renamed from the retired
    # `recall_conversation` when the supervisor went registry-only).
    assert "memory(action, target" in instr
    assert "session_search(query)" in instr
    # ASSUME-INTERRUPTION framing (mirrors Anthropic memory tool default)
    assert "ASSUME INTERRUPTION" in instr


def test_memory_anchor_is_after_proactive_capture():
    """Order matters — anchor goes after PROACTIVE CAPTURE so a
    reader of the prompt encounters trigger-detection rules first
    and the don't-deny-capability anchor right after."""
    import jarvis_agent

    instr = jarvis_agent.JARVIS_INSTRUCTIONS
    pc_idx = instr.find("═══ PROACTIVE CAPTURE")
    anchor_idx = instr.find("═══ YOU HAVE MEMORY ═══")
    drift_idx = instr.find("Memory drift")

    assert pc_idx > 0, "PROACTIVE CAPTURE section missing (prerequisite)"
    assert anchor_idx > pc_idx, "YOU-HAVE-MEMORY must come after PROACTIVE CAPTURE"
    assert drift_idx > anchor_idx, "Memory drift section must remain after YOU-HAVE-MEMORY"
