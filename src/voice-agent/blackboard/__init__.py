"""Truth-grounded blackboard for the JARVIS supervisor v2.

Typed shared-state surface used by:
  - vision_tap (writes screen.* facts)
  - specialists (write tool.* results via task_done)
  - classify_node (writes intent.* records)
  - grounding_gate (reads tool.* evidence to validate supervisor claims)

Spec: docs/superpowers/specs/2026-05-04-truth-grounded-supervisor-design.md
"""
from __future__ import annotations
